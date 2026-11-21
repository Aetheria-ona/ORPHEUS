import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import collision, contacts, content, control, match_audio
from ow174.game.mover import Command, Mover, MoverData
from ow174.game.script import expr
from ow174.lobby import custom_games
from tests.public.test_escort_objective_placeables_contract import FIXTURES
from tools import extract_game_maps


class ControlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((FIXTURES / "control-round-v1.json").read_text(encoding="utf-8"))
        cls.inputs, cls.expected = fixture["input"], fixture["expected"]

    def scene(self, *, legacy=False):
        goals, spawns = [], []
        for stage, position in enumerate(self.inputs["points"]):
            goal = {"stage": stage, "kind": "control point", "placeable": 20 + stage, "position": position}
            if not legacy:
                goal["capture_boxes"] = [[0, 0, 0, 1, *position, *self.inputs["area_half_extents"]]]
                goal["outlines"] = [30 + stage]
            goals.append(goal)
            spawns.extend({"team": team, "stage": stage, "position": [position[0], 0, -8 - team]}
                          for team in (0, 1))
        guid = 0x0800000000000001
        entry = {
            "name": "Control test",
            "modes": [hex(content.CONTROL_MODE)],
            "placeables": [1],
            "mirrored": [20, 21, 22, 23, 30, 31, 32, 33, 40],
            "objectives": goals,
            "spawns": spawns,
        }
        if not legacy:
            room = self.inputs["room"]
            entry.update(
                control_objective_placeables=[20, 21, 22], control_scene_placeables=[30, 31, 32],
                control_healing_rooms=[{"stage": 1, "team": 0, "boxes": room["boxes"]}],
                control_room_placeables=[
                    {"kind": "door", "placeable": 40, "rooms": [[1, 0], [1, 1]]},
                    *({"kind": "overlay", "placeable": room[name], "rooms": [[1, 0]], "viewers": [team]}
                      for team, name in enumerate(("friendly_overlay", "enemy_overlay"))),
                ],
            )
        with patch.object(content, "_map_entries", return_value={guid: entry}):
            return content.game_map(guid, content.CONTROL_MODE)

    def assert_native_phase(self, state, name):
        for scope in (state.entity_vars, state.instance_vars):
            self.assertEqual(scope[59][0], self.expected["native_phases"][name])

    def test_map_mode_filter_and_per_team_spawn_stages(self):
        scene = self.scene()
        self.assertEqual(scene.placeables, (1, 20, 21, 22, 30, 31, 32))
        self.assertEqual(scene.control.room_placeables[0].rooms, ((1, 0), (1, 1)))
        for game_map in (scene, self.scene(legacy=True)):
            for stage, (x, _, _) in enumerate(self.inputs["points"]):
                for team in (0, 1):
                    position, _ = content.spawn_point(game_map, team, 0, stage=stage)
                    self.assertEqual(position, (x, 0, -8 - team))

    def test_room_volumes_and_visibility_follow_current_stage_and_team(self):
        room = self.inputs["room"]
        mode = control.ControlRound(self.scene().control, (1, 2, 0))
        mode.start_setup(0)
        for team, placements in zip((0, 1, content.SPECTATOR_TEAM), self.expected["room_views"], strict=True):
            self.assertEqual(mode.room_visuals(team), {control.escort.MAP_ENTITY | p for p in placements})
        self.assertTrue(mode.in_healing_room(0, room["inside"]))
        self.assertFalse(mode.in_healing_room(1, room["inside"]))
        self.assertFalse(mode.in_healing_room(0, room["outside"]))
        mode.next_round(100)
        self.assertFalse(mode.in_healing_room(0, room["inside"]))
        self.assertFalse(mode.room_visuals(0))
        self.assertFalse(mode.room_visuals(1))

    def test_untagged_door_layers_take_the_restricted_area_stage(self):
        room = self.inputs["room"]
        healing_id, restricted_id = b"heal", b"restricted"
        definitions = [
            {"definition": 0x1806, "position": room["inside"], "capture_area": healing_id},
            {"definition": 0x18D8, "position": room["inside"], "capture_area": restricted_id},
            {"definition": 0x1833, "position": room["inside"]},
            {"definition": 0x1833, "position": room["outside"]},
        ]
        candidates = [
            (0, (0x41C,), {extract_game_maps.ROOM_HEALING_GRAPH: {0x06F1: "TeamBlue"}}),
            (1, (0x41C,), {extract_game_maps.ROOM_RESTRICTED_GRAPH: {}}),
            (2, (), {extract_game_maps.ROOM_VISIBILITY_GRAPH: {0x0877: True}}),
            (3, (), {extract_game_maps.ROOM_VISIBILITY_GRAPH: {0x0877: True}}),
        ]
        zones, room_bounds = extract_game_maps.control_room_areas(
            0, definitions, candidates, {healing_id: room["boxes"], restricted_id: room["boxes"]},
        )
        self.assertEqual(zones, [{"stage": 1, "team": 0, "boxes": room["boxes"]}])
        placements, hidden, _ = extract_game_maps.objective_rooms(
            0, definitions, candidates, (), control=True, restricted_rooms=room_bounds,
            hide_unassigned_overlays=True,
        )
        self.assertEqual(hidden, {2, 3})
        self.assertEqual(placements, [{"placeable": 2, "rooms": [(1, 0)], "kind": "overlay", "viewers": [1]}])

    def test_collision_panel_uses_the_opposing_view_and_fits_the_paired_rim(self):
        case, expected = self.inputs["room_panel"], self.expected["room_panel"]
        panel = {"definition": 0x0400000000001832, "position": case["position"], "scale": case["scale"],
                 "rotation": (0, 0, 0, 1)}
        rim = {"definition": 0x0400000000001D13, "position": case["rim_position"], "scale": case["rim_scale"],
               "rotation": (0, 0, 0, 1)}
        visibility = extract_game_maps.ROOM_VISIBILITY_GRAPH
        candidates = [(0, (), {visibility: {}}), (1, (), {visibility: {0x0878: True}})]
        bounds = [((2, 0), [(0, 0, 0, 1, 0, 0, 0, 4, 4, 4)])]
        placements, hidden, _ = extract_game_maps.objective_rooms(
            0, [panel, rim], candidates, (), control=True, restricted_rooms=bounds,
            hide_unassigned_overlays=True,
        )
        self.assertEqual(hidden, {0, 1})
        fitted, frame = placements
        self.assertEqual(fitted["viewers"], expected["viewers"])
        self.assertEqual(frame["viewers"], expected["rim_viewers"])
        self.assertEqual(fitted["placement_definition"], panel["definition"])
        self.assertEqual(fitted["position"], expected["position"])
        self.assertEqual(fitted["scale"], expected["scale"])

    def test_custom_mode_rates_and_valid_point_are_carried_into_the_match_scene(self):
        case = self.inputs["custom_rules"]
        expected = self.expected["custom_rules"]

        def rules(rows):
            return {**custom_games.mode_rules(content.CONTROL_MODE), "+0xD8": [
                dict(zip(("+0x0", "+0x4", "+0x6"), row, strict=True)) for row in rows
            ]}

        scene = self.scene()
        room = custom_games.CustomRoom.create("Control contract")
        room.rules = custom_games.RoomRules(
            maps=[{"+0x0": rules(case["map"]), "+0x138": scene.map_guid}], modes=[rules(case["mode"])],
        )
        with (patch.object(custom_games, "game_map", return_value=scene),
              patch.object(custom_games, "has_collision", return_value=True)):
            setup, _ = room.prepared_scene()
        self.assertEqual(setup.control.capture_scale, expected["capture_scale"])
        self.assertEqual(setup.control.score_scale, expected["score_scale"])
        self.assertEqual(setup.control.fixed_stage, expected["fixed_stage"])
        self.assertEqual(custom_games.RoomRules(modes=[rules([])]).control_settings(rules([])), {})

        mode = control.ControlRound(setup.control, (1, 1, 1))
        mode.start_setup(0)
        unlock = mode.deadline + control.UNLOCK_SECONDS
        point = mode.points[mode.stage].position
        mode.advance(unlock, [])
        captured_at = unlock + case["capture_seconds"]
        mode.advance(captured_at, [(1, 0, point)])
        self.assertEqual(mode.owner, 0)
        mode.advance(captured_at + case["scoring_sample_seconds"], [])
        self.assertEqual(mode.scores[0], expected["sample_score"])

        door = next(iter(mode.doors))
        opened = mode.door_state(door, captured_at, None)
        mode.next_round(mode.deadline)
        self.assertEqual(mode.stage, expected["fixed_stage"])
        self.assertFalse(mode.door_open(door))
        closed = mode.door_state(door, mode.deadline, opened)
        self.assertEqual(closed.pose, control.escort.DoorPose.CLOSED)
        self.assertEqual(closed.states, {control.escort.DOOR_CLOSE_ANIM, control.escort.DOOR_CLOSE_ALIAS})

    def test_three_rounds_overtime_retakes_and_winning_two(self):
        mode = control.ControlRound(self.scene().control)
        self.assertEqual(mode.points_to_win, self.expected["wins_needed"])
        setup = self.inputs["setup_starts"]
        first = mode.mode_state(setup - 1, None, hero_select_ready=True)
        self.assertEqual(first.phase, control.Phase.WAITING)
        self.assert_native_phase(first, "WAITING")
        self.assertNotIn(control.escort.COUNTDOWN, first.entity_vars)
        mode.start_setup(setup)
        self.assertEqual(mode.deadline - setup, self.expected["setup_seconds"])
        self.assertEqual(mode.point_state(0).entity_vars[control.escort.OBJECTIVE_KIND][0],
                         expr.Asset(int(self.expected["point_kind"], 16)))
        opening = []
        for now, teams in self.inputs["opening"]:
            position = mode.points[0].position
            mode.advance(now, ((body, team, position) for body, team in enumerate(teams, 1)))
            opening.append([mode.phase.name, mode.owner, round(mode.progress, 4),
                            *(round(score, 4) for score in mode.scores), mode.contested, mode.overtime])
        self.assertEqual(opening, self.expected["opening"])
        self.assertIsNone(mode.winner)
        self.assertEqual(mode.round_winner, 0)
        self.assertFalse(mode.point_state(0).waypoint_visible)
        with patch.object(control.graph, "graph", return_value=object()):
            result = mode.mode_state(mode.deadline, setup, hero_select_ready=True)
        self.assert_native_phase(result, "ROUND_RESULT")

        for stage, team in ((1, 1), (2, 0)):
            mode.next_round(mode.deadline)
            self.assertEqual(mode.stage, stage)
            self.assertEqual(mode.phase, control.Phase.SETUP)
            self.assertEqual(mode.owner, control.NEUTRAL)
            self.assertEqual(mode.scores, [0, 0])
            unlock = mode.deadline + control.UNLOCK_SECONDS
            point = mode.points[stage].position
            high = (point[0], point[1] + 10, point[2])
            mode.advance(unlock, [])
            mode.advance(unlock + 10, [(1, team, high), (2, team, mode.points[stage - 1].position)])
            self.assertEqual(mode.progress, 0)
            mode.advance(unlock + 18, [(1, team, point)])
            mode.advance(unlock + 138, [])
            self.assertEqual(mode.round_winner, team)
            expected_phase = control.Phase.ROUND_END if stage == 1 else control.Phase.COMPLETE
            self.assertEqual(mode.phase, expected_phase)
            mode.advance(mode.deadline, [])
        self.assertEqual(mode.round_wins, self.expected["round_wins"])
        self.assertEqual(mode.winner, 0)
        result = mode.mode_state(mode.deadline, 0, hero_select_ready=True)
        self.assert_native_phase(result, "MATCH_RESULT")
        self.assertEqual(result.instance_vars[0x1D65][0], 0)
        self.assertEqual(mode.scores[mode.winner], 100)
        expected_result = self.expected["match_result"]
        self.assertEqual(result.instance_vars[0x1330][0], expected_result["last_hud_percent"])
        self.assertTrue(set(expected_result["phase_receiver_states"]).issubset(result.receivers))

    def test_round_end_holds_final_point_then_releases_into_summary(self):
        phase = control.Phase
        mode = control.ControlRound(self.scene().control, (2, 0, 1))
        mode.start_setup(0)
        mode.advance(60, [])
        mode.owner = 1
        mode.scores = [8, 99]
        live = tuple(mode.mode_state(60, 0, hero_select_ready=True, viewer_team=viewer) for viewer in (0, 1))
        mode.advance(62, [])
        self.assertEqual(mode.phase, phase.ROUND_END)
        self.assertAlmostEqual(mode.deadline - 62, self.expected["round_summary_delay"])
        self.assertEqual(mode.movement_scale, self.expected["round_end_scale"])
        self.assertFalse(mode.point_state(2).waypoint_visible)
        self.assertTrue(mode.point_state(2).presentation[0])
        for viewer, banner in ((0, (0, 1)), (1, (1, 0))):
            state = mode.mode_state(62, 0, hero_select_ready=True, viewer_team=viewer, previous=live[viewer])
            self.assert_native_phase(state, "ROUND_END")
            self.assertEqual(state.entity_vars[0x09C3][0], (0, 1))
            self.assertEqual(state.owner, 1)
            self.assertEqual(state.instance_vars[0x1332][0], self.expected["round_hud_owner"])
            self.assertEqual(state.instance_vars[0x1331][0], 100)
            self.assertIn(control.OBJECTIVE_HUD_LIVE, state.states)
            self.assertFalse(state.entity_vars[control.UNLOCK_CLOCK_VISIBLE][0])
            self.assertEqual((state.entity_vars[0x1846][0], state.entity_vars[0x1847][0]), banner)
            self.assertIsNone(state.round_result)
            actions = state.round_actions_from(live[viewer])
            self.assertEqual(list(actions), self.expected["round_result_actions"])
            self.assertNotIn(control.MATCH_RESULT_MUSIC, state.states)
            switched = mode.mode_state(
                62.1, 0, hero_select_ready=True, viewer_team=1 - viewer, previous=state,
            )
            self.assertEqual((switched.entity_vars[0x1846][0], switched.entity_vars[0x1847][0]), banner)
        summary_at = mode.deadline
        mode.advance(summary_at - 0.001, [])
        self.assertEqual(mode.phase, phase.ROUND_END)
        self.assertEqual(mode.movement_scale, self.expected["round_end_scale"])
        self.assertFalse(mode.point_state(2).waypoint_visible)
        hold = mode.mode_state(summary_at - 0.001, 0, hero_select_ready=True)
        self.assertFalse(hold.round_actions_from(state))
        mode.advance(summary_at, [])
        self.assertEqual(mode.phase, phase.COMPLETE)
        self.assertAlmostEqual(mode.deadline - summary_at, self.expected["intermission_seconds"])
        with patch.object(control.graph, "graph", return_value=object()):
            summary = mode.mode_state(summary_at, 0, hero_select_ready=True)
        self.assertFalse(summary.round_actions_from(hold))
        self.assertEqual(summary.instance_vars[0x1332][0], self.expected["round_hud_owner"])
        self.assertEqual(summary.instance_vars[0x1331][0], 100)
        self.assertIn(control.OBJECTIVE_HUD_LIVE, summary.states)
        self.assertFalse(summary.entity_vars[control.UNLOCK_CLOCK_VISIBLE][0])
        self.assertNotIn(control.MATCH_RESULT_MUSIC, summary.states)
        self.assertEqual(mode.movement_scale, 1.0)
        self.assertFalse(mode.point_state(2).waypoint_visible)
        mode.next_round(mode.deadline)
        following = mode.mode_state(mode.deadline - 30, 0, hero_select_ready=True)
        self.assertNotIn(control.OBJECTIVE_HUD_LIVE, following.states)
        self.assertFalse(mode.point_state(2).waypoint_visible)
        self.assertTrue(mode.point_state(0).waypoint_visible)
        self.assertTrue(mode.point_state(0).presentation[0])

    def test_submap_order_tracks_rounds_separately_from_point_labels(self):
        mode = control.ControlRound(self.scene().control, (2, 0, 1))
        self.assertEqual((mode.stage, mode.round_index), (2, 0))
        mode.next_round(10)
        self.assertEqual((mode.stage, mode.round_index), (0, 1))
        self.assertFalse(mode.door_open(control.escort.MAP_ENTITY | 40))
        mode.next_round(20)
        self.assertEqual((mode.stage, mode.round_index), (1, 2))
        self.assertEqual(mode.phase, control.Phase.SETUP)
        self.assertFalse(mode.door_open(control.escort.MAP_ENTITY | 40))
        mode.advance(mode.deadline, [])
        self.assertEqual(mode.phase, control.Phase.UNLOCK)
        self.assertTrue(mode.door_open(control.escort.MAP_ENTITY | 40))

    def test_score_to_win_three_allows_fourth_and_fifth_rounds(self):
        case = self.inputs["score_to_win"]
        scene = replace(self.scene().control, points_to_win=case["setting"])
        mode = control.ControlRound(scene, (2, 0, 1))
        for index, (stage, winner) in enumerate(zip(case["stages"], case["winners"], strict=True)):
            self.assertEqual(mode.stage, stage)
            mode._finish_round(winner, index * 200.0)
            if index < len(case["winners"]) - 1:
                self.assertIsNone(mode.winner)
                mode.next_round(mode.deadline + 8)
        self.assertEqual(mode.winner, case["winners"][-1])
        self.assertEqual(mode.phase, control.Phase.COMPLETE)

    def test_native_actions_cover_opening_clocks_and_integer_score_steps(self):
        mode = control.ControlRound(self.scene().control)
        previous = mode.mode_state(0, 30, hero_select_ready=True)
        self.assert_native_phase(previous, "ASSEMBLE")
        actions = []
        for seconds in self.inputs["assembly_clock"]:
            state = mode.mode_state(30 - seconds, 30, hero_select_ready=True)
            actions.append(state.action_from(previous))
            previous = state
        self.assertEqual(actions, self.expected["assembly_actions"])

        mode.start_setup(30)
        previous = mode.mode_state(30, 30, hero_select_ready=True)
        self.assert_native_phase(previous, "SETUP")
        self.assertEqual(mode.point_state(0).waypoint_visible, self.expected["setup_waypoint_visible"])
        self.assertFalse(any(mode.point_state(stage).waypoint_visible for stage in (1, 2)))
        self.assertTrue(mode.point_state(0).presentation[0])
        actions = []
        music = []
        for seconds in self.inputs["setup_clock"]:
            state = mode.mode_state(mode.deadline - seconds, 30, hero_select_ready=True)
            actions.append(state.action_from(previous))
            music.append(state.setup_music_from(previous))
            self.assertFalse(state.entity_vars[control.UNLOCK_CLOCK_VISIBLE][0])
            previous = state
        self.assertEqual(actions, self.expected["setup_actions"])
        self.assertEqual(music, self.expected["setup_music"])
        mode.advance(mode.deadline, [])
        self.assertEqual(mode.deadline - 60, self.expected["unlock_seconds"])
        point_type = mode.point_state(0).instance_vars[control.assault.POINT_WAYPOINT_TYPE][0]
        self.assertEqual(point_type, expr.Asset(int(self.expected["point_marker"], 16)))
        actions = []
        for seconds in self.inputs["unlock_clock"]:
            state = mode.mode_state(mode.deadline - seconds, 30, hero_select_ready=True)
            self.assert_native_phase(state, "UNLOCK")
            actions.append(state.action_from(previous))
            self.assertTrue(mode.point_state(0).presentation[0])
            self.assertTrue(state.entity_vars[control.UNLOCK_CLOCK_VISIBLE][0])
            previous = state
        self.assertEqual(actions, self.expected["unlock_actions"])
        before = mode.mode_state(mode.deadline - 3.25, 30, hero_select_ready=True)
        expired = mode.mode_state(mode.deadline - 2.75, 30, hero_select_ready=True)
        self.assertIsNone(expired.action_from(before))

        mode.advance(mode.deadline, [])
        self.assertFalse(mode.point_state(0).presentation[0])
        mode.owner = 0
        previous = mode.mode_state(100, 30, hero_select_ready=True)
        self.assert_native_phase(previous, "ACTIVE")
        actions = []
        for score in self.inputs["end_scores"]:
            mode.scores[0] = score
            if score == 100:
                mode._finish_round(0, 100)
            state = mode.mode_state(100, 30, hero_select_ready=True)
            self.assertIn(control.SCORE_SOUND_SCOPE, state.states)
            actions.append(state.action_from(previous))
            previous = state
        self.assertEqual(actions, self.expected["end_actions"])

    def test_closed_spawn_door_supports_mover_until_open(self):
        sample = self.inputs["door_support"]
        scene = self.scene().control
        placement = replace(scene.room_placeables[0], collision=(
            ("sphere", collision.MOVER, (0, 0, 0), sample["radius"]),
        ), position=tuple(sample["center"]))
        mode = control.ControlRound(replace(scene, room_placeables=(placement,)), (1, 0, 2))
        x, _, z = sample["center"]
        y = self.expected["door_top"]
        roof = y + 0.9
        triangle = (x - 2, roof, z - 2, x + 2, roof, z - 2, x, roof, z + 2)
        base = collision.World([triangle], surfaces=[collision.NOT_GROUND], engine=contacts.StaticWorld([]))
        world = mode.collision_world(base)
        mode.start_setup(0)
        self.assertIsNone(base.ground(x, z, y + 0.2, y - 0.2))
        self.assertEqual(base.ground(x, z, y + 1, y - 0.2).surface, collision.NOT_GROUND)
        ground = world.ground(x, z, y + 1, y - 0.2)
        self.assertAlmostEqual(ground.y, y, delta=0.02)
        mover = Mover((x, ground.y, z), 0, world, MoverData(), category=collision.TEAM1)
        for tick in range(1, sample["ticks"] + 1):
            mover.step(Command(tick), 0.016)
            self.assertFalse(mover.airborne)
            self.assertAlmostEqual(mover.position[1], y, delta=0.02)
        self.assertIsNone(mode.payload_collision.rider(mover))
        mode.advance(mode.deadline, [])
        self.assertIsNone(world.ground(x, z, y + 0.2, y - 0.2))
        self.assertEqual(world.engine.query((x - 1, y - 1, z - 1), (x + 1, y + 1, z + 1)), [])

    def test_control_speech_follows_phases_and_announces_both_scores(self):
        phase = control.Phase
        assembly = match_audio.Timeline(phase.ASSEMBLE, 1, 0)
        setup = match_audio.Timeline(phase.SETUP, 30, 1)
        unlock = match_audio.Timeline(phase.UNLOCK, 30, 1)
        active = match_audio.Timeline(phase.ACTIVE, 0, 1)
        outcome = match_audio.Timeline(phase.COMPLETE, 0, 1, control_wins=(1, 0))
        round_end = match_audio.Timeline(phase.ROUND_END, 0, 1, round_winner=0)
        for team in (0, 1):
            cases = (
                (None, setup, ()),
                (assembly, setup, ()),
                (setup, unlock, (match_audio.CONTROL_ROUNDS[1],)),
                (unlock, active, (match_audio.CONTROL_UNLOCKED,)),
                *((unlock, replace(unlock, seconds=s), ()) for s in (20, 10, 5, 4, 3, 2, 1)),
                (outcome, replace(setup, stage=2), ()),
            )
            for previous, current, expected in cases:
                cues = match_audio.speech(
                    previous, current, content.CONTROL_MODE, team, single_countdown=False,
                )
                self.assertEqual(cues, expected)
            score = match_audio.speech(round_end, outcome, content.CONTROL_MODE, team, single_countdown=False)
            self.assertEqual([f"{line:04X}" for _, line in score], self.expected["score_lines"][team])
            for index, lines in enumerate(self.expected["round_end_lines"]):
                closing = replace(round_end, stage=index)
                live = replace(active, stage=index)
                cues = match_audio.speech(live, closing, content.CONTROL_MODE, team, single_countdown=False)
                self.assertEqual([f"{line:04X}" for _, line in cues], [lines[team]])
                for previous in (None, closing):
                    cues = match_audio.speech(previous, closing, content.CONTROL_MODE, team,
                                              single_countdown=False)
                    self.assertEqual(cues, ())
            previous = setup
            setup_lines = []
            for seconds in range(5, 0, -1):
                current = replace(setup, seconds=seconds)
                cues = match_audio.speech(
                    previous, current, content.CONTROL_MODE, team, single_countdown=False,
                )
                setup_lines.extend(f"{line:04X}" for _, line in cues)
                previous = current
            self.assertEqual(setup_lines, self.expected["setup_lines"])

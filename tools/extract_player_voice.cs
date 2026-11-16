using DataTool;
using DataTool.Helper;
using TankLib;
using TankLib.Chunks;
using TankLib.STU;
using TankLib.STU.Types;
using System.Text.Json;
using D = DataTool.Program;

if (args.Length != 2) throw new ArgumentException("Usage: extract-player-voice <game directory> <runtime data directory>");

D.Flags = new ToolFlags { OverwatchDirectory = args[0], Language = "enUS", SpeechLanguage = "enUS", NoLanguageRegistry = true };
D.InitStorage(false);
if (D.BuildVersion != 104319) throw new InvalidOperationException($"Expected 104319, found {D.BuildVersion}");
string Hex(ulong key) => $"0x{key:X16}";
var criteria = new SortedDictionary<string, List<string>>();
foreach (var key in D.TrackedFiles[0x78].Order()) {
    using var stu = STUHelper.OpenSTUSafe(key);
    foreach (var criterion in stu?.GetInstances<STUCriteria_Statescript>() ?? []) {
        ulong id = criterion.m_identifier;
        if (id == 0) continue;
        if (!criteria.TryGetValue(Hex(id), out var stimuli)) criteria[Hex(id)] = stimuli = [];
        stimuli.Add(Hex(key));
    }
}
// Criteria referenced by the target build's 05AD and 0686 communication graphs.
var authored = new HashSet<ulong>(new ulong[]{0xAEB,0x1073,0x1078,0x74BD,0x74BE,0x1102,0x74BC,0x1079,
     0x74BF,0x74C0,0x721,0x725,0x722,0x726,0x71F,0x720,0x1A47,0x74C5,0x1070,0x106C,0x106D,
     0x1C26,0x1C2B,0x1A45,0x1A46,0x724,0x5F3,0x1C2E}.Select(x=>x|0x0D80000000000000UL));
var byIdentifier = criteria.Where(p=>authored.Contains(Convert.ToUInt64(p.Key[2..],16)))
    .ToDictionary(p=>p.Key,p=>p.Value);
var effects = new SortedDictionary<string, string>();
foreach (var key in D.TrackedFiles[0xA5].Order()) {
    var unlock = STUHelper.GetInstance<STUUnlock_VoiceLine>(key);
    if (unlock?.m_F57B051E != null && teResourceGUID.Type(unlock.m_F57B051E) == 0xD)
        effects[Hex(key)] = Hex(unlock.m_F57B051E);
}
var effectStimuli = new SortedDictionary<string, string>();
foreach (var effect in effects.Values.Distinct()) {
    using var stream = IO.OpenFile(Convert.ToUInt64(effect[2..],16));
    if (stream == null) continue;
    using var chunks = new teChunkedData(stream, true);
    if (chunks.Chunks == null) continue;
    var stimuli = chunks.EnumerateChunks<teEffectComponentVoiceStimulus>()
        .Select(chunk => chunk.Header.VoiceStimulus.GUID).Distinct().ToArray();
    if (stimuli.Length == 1 && teResourceGUID.Type(stimuli[0]) == 0x78)
        effectStimuli[effect] = Hex(stimuli[0]);
}
var supportedStimuli = byIdentifier.Values.SelectMany(list=>list).Concat(effectStimuli.Values).ToHashSet();

// Voice set field F7FA2CAC stores the 104319 line-key array in voice-row order.
ulong[] LineKeys(Stream stream, teStructuredData stu) {
    stream.Position = 24;
    using var reader = new BinaryReader(stream, System.Text.Encoding.UTF8, true);
    var dynSize = reader.ReadInt32();
    var dynStart = reader.ReadInt32();
    var dataStart = reader.ReadInt32();
    stream.Position = dataStart;
    var bag = stu.FieldInfoBags[reader.ReadInt32()];
    foreach (var field in bag) {
        int size = (int)field.Size;
        if (size == 0) size = reader.ReadInt32();
        if (field.Hash != 0xF7FA2CAC) {
            stream.Position += size;
            continue;
        }
        var offset = reader.ReadInt32();
        if (offset < 0 || offset + 16 > dynSize) break;
        stream.Position = dynStart + offset;
        var count = reader.ReadInt32();
        reader.ReadInt32();
        var start = reader.ReadInt64();
        if (count <= 0 || start < 0 || start + count * 8L > dynSize) break;
        stream.Position = dynStart + start;
        return Enumerable.Range(0, count).Select(_=>reader.ReadUInt64()).ToArray();
    }
    return [];
}
var baseSets = new SortedDictionary<string, string>();
foreach (var key in D.TrackedFiles[0x75].Order()) {
    var hero = STUHelper.GetInstance<STUHero>(key);
    ulong set = STUHelper.GetInstance<STUVoiceSetComponent>(hero?.m_gameplayEntity)?.m_voiceDefinition;
    if (set != 0) baseSets[Hex(key)] = Hex(set);
}
var baseSetIds = baseSets.Values.Select(s=>Convert.ToUInt64(s[2..],16)).ToHashSet();
var skinSets = new SortedDictionary<string, SortedDictionary<string, string>>();
foreach (var (skin, graph) in D.TankHandler.m_resourceGraph.m_skins.OrderBy(p=>p.Key)) {
    var replacements = new SortedDictionary<string,string>();
    foreach (var row in graph.m_assets) {
        if (baseSetIds.Contains(row.m_srcAsset) && teResourceGUID.Type(row.m_destAsset)==0x5F)
            replacements[Hex(row.m_srcAsset)] = Hex(row.m_destAsset);
    }
    if (replacements.Count>0) skinSets[Hex(skin)] = replacements;
}
var targetSets = baseSets.Values.Concat(skinSets.Values.SelectMany(s=>s.Values)).ToHashSet();
var voiceSets = new SortedDictionary<string, SortedDictionary<string, List<string>>>();
foreach (var key in D.TrackedFiles[0x5F].Where(k=>targetSets.Contains(Hex(k))).Order()) {
    using var stream = IO.OpenFile(key);
    if (stream == null) continue;
    using var buffer = new MemoryStream();
    stream.CopyTo(buffer);
    buffer.Position = 0;
    using var stu = new teStructuredData(buffer, true);
    var voice = stu.GetInstance<STUVoiceSet>();
    if (voice?.m_voiceLineInstances == null) continue;
    var keys = LineKeys(buffer,stu);
    if (keys.Length != voice.m_voiceLineInstances.Length) continue;
    var found = new SortedDictionary<string,List<string>>();
    for (int j = 0; j < keys.Length; j++) {
        var row = voice.m_voiceLineInstances[j];
        var line = row.m_voiceLineRuntime;
        ulong stim = line?.m_stimulus;
        if (!supportedStimuli.Contains(Hex(stim)) || teResourceGUID.Type(keys[j]) != 0x6F) continue;
        if (!found.TryGetValue(Hex(stim), out var variants)) found[Hex(stim)] = variants = [];
        if (!variants.Contains(Hex(keys[j]))) variants.Add(Hex(keys[j]));
    }
    if (found.Count>0) voiceSets[Hex(key)] = found;
}
var data = new { criteria = byIdentifier, heroes = baseSets, skins = skinSets, sets = voiceSets,
    equipped = effectStimuli };
File.WriteAllText(Path.Combine(args[1],"player_voice_174.json"),
    JsonSerializer.Serialize(data,new JsonSerializerOptions{WriteIndented=true})+"\n");
Console.WriteLine($"RESULT criteria={byIdentifier.Count} heroes={baseSets.Count} skins={skinSets.Count} sets={voiceSets.Count}");

File.WriteAllText(Path.Combine(args[1], "voice_effects_174.json"),
    JsonSerializer.Serialize(effects, new JsonSerializerOptions { WriteIndented = true }) + "\n");
Console.WriteLine($"Extracted {effects.Count} cosmetic voice unlocks and {effectStimuli.Count} effect cues.");

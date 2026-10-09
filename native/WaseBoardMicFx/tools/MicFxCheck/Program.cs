using System.IO.MemoryMappedFiles;
using NAudio.CoreAudioApi;
using WaseBoard.Services.MicFx;

// Écoute le micro quelques secondes (ce qui fait charger l'effet par Windows), envoie le bip de test
// (880 Hz) et cherche ce bip dans le signal capté. Rien n'est enregistré sur disque.
//
//   dotnet run --project native/WaseBoardMicFx/tools/MicFxCheck [-- "nom partiel du micro"] [--echo]
//
// --echo : mesure en plus l'effet de l'annulation d'écho de Windows (voir EchoCheck.cs).

const string MicFeedRingName = @"Global\WaseBoardMicFeed";

string Header()
{
    try
    {
        using var mapping = MemoryMappedFile.OpenExisting(MicFeedRingName);
        using var view = mapping.CreateViewAccessor(0, 64);
        var age = Environment.TickCount64 - view.ReadInt64(24);
        return $"présente (writePos={view.ReadInt64(16)}, dernier passage de l'effet il y a {age} ms, {view.ReadUInt32(32)} Hz / {view.ReadUInt32(36)} canaux)";
    }
    catch (Exception ex) { return "ABSENTE (" + ex.GetType().Name + ")"; }
}

using var enumerator = new MMDeviceEnumerator();
var micName = args.FirstOrDefault(a => !a.StartsWith("--"));
var mic = micName is not null
    ? enumerator.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active).First(d => d.FriendlyName.Contains(micName, StringComparison.OrdinalIgnoreCase))
    : enumerator.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Communications);
Console.WriteLine($"Micro : {mic.FriendlyName}");

using var capture = new WasapiCapture(mic, false, 20);
var format = capture.WaveFormat;
if (format.BitsPerSample != 32)
{
    Console.WriteLine($"Format de capture inattendu ({format}) : l'outil attend du float 32 bits.");
    return 2;
}
var samples = new List<float>(); // premier canal uniquement, en mémoire
capture.DataAvailable += (_, e) =>
{
    var frameBytes = 4 * format.Channels;
    lock (samples)
        for (var i = 0; i + frameBytes <= e.BytesRecorded; i += frameBytes)
            samples.Add(BitConverter.ToSingle(e.Buffer, i));
};

capture.StartRecording();
Thread.Sleep(1500);
Console.WriteLine($"Mémoire partagée pendant l'écoute : {Header()}");

using (var feed = new MicFeedService())
{
    feed.PlayTestTone();
    Thread.Sleep(1200);
    Console.WriteLine($"Mémoire partagée pendant le bip : {Header()}");
    Thread.Sleep(1300);
}
capture.StopRecording();
Thread.Sleep(200);

// Amplitude d'une fréquence sur une fenêtre (algorithme de Goertzel).
static double Amplitude(List<float> s, int start, int count, double frequency, int rate)
{
    double w = 2 * Math.PI * frequency / rate, c = 2 * Math.Cos(w), s1 = 0, s2 = 0;
    for (var i = start; i < start + count; i++) { var s0 = s[i] + c * s1 - s2; s2 = s1; s1 = s0; }
    return Math.Sqrt(Math.Max(s1 * s1 + s2 * s2 - c * s1 * s2, 0)) * 2 / count;
}

var detected = false;
lock (samples)
{
    var rate = format.SampleRate;
    var window = rate / 5;
    for (var t = 0; t + window <= samples.Count; t += window)
    {
        var tone = Amplitude(samples, t, window, 880, rate);
        var control = Amplitude(samples, t, window, 700, rate);
        var hit = tone > 0.02 && tone > 5 * control;
        detected |= hit;
        Console.WriteLine($"  {t / (double)rate,4:F1} s   880 Hz = {tone:F4}   témoin 700 Hz = {control:F4}{(hit ? "   <-- bip" : "")}");
    }
}
if (args.Contains("--echo"))
{
    Console.WriteLine("Annulation d'écho :");
    EchoCheck.Run(mic);
}
Console.WriteLine(detected ? "OK : le bip est bien dans le micro." : @"ÉCHEC : pas de bip dans le micro (voir le journal de l'effet, C:\ProgramData\WaseBoard\MicFx\apo.log).");
return detected ? 0 : 1;

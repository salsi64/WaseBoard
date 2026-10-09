using System.Reflection;
using System.Runtime.InteropServices;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;
using WaseBoard.Services.MicFx;

// Option --echo : mesure l'effet de l'annulation d'écho et de la suppression de bruit de Windows sur
// les sons injectés. Le micro est écouté dans plusieurs catégories de flux (comme le ferait un jeu, Discord
// ou un assistant vocal), avec ou sans le même bip joué en retour local (« M'entendre aussi »).
internal static class EchoCheck
{
    private static readonly (string Name, int Category, bool Monitor)[] Cases =
    {
        ("Défaut, sans retour local", 0, false),
        ("Défaut, avec retour local", 0, true),
        ("Communications, sans retour local", 3, false),
        ("Communications, avec retour local", 3, true),
        ("Chat de jeu, avec retour local", 8, true),
        ("Speech, sans retour local", 9, false),
        ("Speech, avec retour local", 9, true),
    };

    public static void Run(MMDevice mic)
    {
        using var enumerator = new MMDeviceEnumerator();
        var speakers = enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
        Console.WriteLine($"Retour local sur : {speakers.FriendlyName}");
        using var feed = new MicFeedService();
        foreach (var (name, category, monitor) in Cases)
        {
            var level = Measure(mic, speakers, feed, category, monitor, out var note);
            var db = level > 0 ? 20 * Math.Log10(level / 0.25) : double.NegativeInfinity;
            Console.WriteLine($"  {name,-36} 880 Hz = {level:F4}  ({db:0.0} dB par rapport à l'injecté){note}");
            Thread.Sleep(600);
        }
    }

    public static double Measure(MMDevice mic, MMDevice speakers, MicFeedService feed, int category, bool monitor, out string note)
    {
        note = "";
        using var capture = new WasapiCapture(mic, false, 20);
        if (category != 0)
        {
            var field = typeof(WasapiCapture).GetFields(BindingFlags.NonPublic | BindingFlags.Instance).First(f => f.FieldType == typeof(AudioClient));
            var client = (AudioClient)field.GetValue(capture)!;
            var raw = typeof(AudioClient).GetField("audioClientInterface", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(client)!;
            var props = new ClientProps { cbSize = (uint)Marshal.SizeOf<ClientProps>(), eCategory = category };
            var hr = ((IAudioClient2Full)raw).SetClientProperties(ref props);
            if (hr != 0) note = $"  [SetClientProperties 0x{hr:X8}]";
        }
        var format = capture.WaveFormat;
        var samples = new List<float>();
        capture.DataAvailable += (_, e) =>
        {
            var frame = 4 * format.Channels;
            lock (samples) for (var i = 0; i + frame <= e.BytesRecorded; i += frame) samples.Add(BitConverter.ToSingle(e.Buffer, i));
        };
        capture.StartRecording();
        Thread.Sleep(1200);
        int start; lock (samples) start = samples.Count;

        WasapiOut? output = null;
        if (monitor)
        {
            output = new WasapiOut(speakers, AudioClientShareMode.Shared, true, 50);
            output.Init(new SignalGenerator(48000, 1) { Frequency = 880, Gain = 0.25 }.Take(TimeSpan.FromSeconds(1.5)));
            output.Play();
        }
        feed.PlayTestTone();
        Thread.Sleep(1700);
        capture.StopRecording();
        output?.Dispose();
        Thread.Sleep(150);

        lock (samples)
        {
            // Fenêtre centrale du bip (0,4 s à 1,2 s après son début), pour ignorer les latences.
            var rate = format.SampleRate;
            var from = start + (int)(0.4 * rate);
            var count = Math.Min((int)(0.8 * rate), samples.Count - from);
            if (count <= 0) return 0;
            double w = 2 * Math.PI * 880 / rate, c = 2 * Math.Cos(w), s1 = 0, s2 = 0;
            for (var i = from; i < from + count; i++) { var s0 = samples[i] + c * s1 - s2; s2 = s1; s1 = s0; }
            return Math.Sqrt(Math.Max(s1 * s1 + s2 * s2 - c * s1 * s2, 0)) * 2 / count;
        }
    }
}

[StructLayout(LayoutKind.Sequential)]
internal struct ClientProps { public uint cbSize; public int bIsOffload; public int eCategory; public int Options; }

// IAudioClient2 complet (les méthodes d'IAudioClient doivent figurer pour que la table virtuelle soit juste).
[ComImport, Guid("726778CD-F60A-4eda-82DE-E47610CD78AA"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAudioClient2Full
{
    [PreserveSig] int Initialize(int shareMode, int flags, long duration, long periodicity, IntPtr format, IntPtr session);
    [PreserveSig] int GetBufferSize(out uint size);
    [PreserveSig] int GetStreamLatency(out long latency);
    [PreserveSig] int GetCurrentPadding(out uint padding);
    [PreserveSig] int IsFormatSupported(int shareMode, IntPtr format, IntPtr closest);
    [PreserveSig] int GetMixFormat(out IntPtr format);
    [PreserveSig] int GetDevicePeriod(out long defaultPeriod, out long minimumPeriod);
    [PreserveSig] int Start();
    [PreserveSig] int Stop();
    [PreserveSig] int Reset();
    [PreserveSig] int SetEventHandle(IntPtr handle);
    [PreserveSig] int GetService(ref Guid iid, out IntPtr service);
    [PreserveSig] int IsOffloadCapable(int category, out int capable);
    [PreserveSig] int SetClientProperties(ref ClientProps properties);
    [PreserveSig] int GetBufferSizeLimits(IntPtr format, int eventDriven, out long min, out long max);
}

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace WaseBoard.Services.MicFx
{
    /// <summary>Joue les sons « dans le micro » : les décode, les mixe en 48 kHz mono et alimente la
    /// mémoire partagée lue par l'effet micro (APO), au rythme de l'horloge. Le vrai micro ne passe
    /// jamais par WaseBoard : c'est Windows qui y ajoute ce mélange, pour toutes les applications.
    ///
    /// Aucun thread ne tourne tant qu'aucun son ne joue ; une « session » d'écriture commence au
    /// premier son et se termine peu après le dernier.</summary>
    public sealed class MicFeedService : IDisposable
    {
        public const string TestToneId = "__micfx_test__";

        private static readonly WaveFormat MixFormat = WaveFormat.CreateIeeeFloatWaveFormat(MicFeedRing.SampleRate, 1);

        private readonly MixingSampleProvider _mixer = new(MixFormat) { ReadFully = true };
        private readonly Dictionary<string, Input> _inputs = new();
        private readonly object _lock = new();
        private readonly AutoResetEvent _wake = new(false);
        private Thread? _thread;
        private volatile bool _disposed;
        private MicFeedRing? _ring;

        /// <summary>Gain appliqué au mélange envoyé dans le micro (réglage « Volume dans le micro »).</summary>
        public float Volume { get; set; } = 1.0f;

        /// <summary>Un son a fini de jouer dans le micro (ou a été coupé). Appelé hors thread UI.</summary>
        public event Action<string>? SoundEnded;

        public MicFeedService()
        {
            _mixer.MixerInputEnded += (_, e) =>
            {
                if (e.SampleProvider is not Input input) return;
                lock (_lock)
                {
                    if (_inputs.TryGetValue(input.SoundId, out var current) && ReferenceEquals(current, input))
                        _inputs.Remove(input.SoundId);
                }
                input.Dispose();
                SoundEnded?.Invoke(input.SoundId);
            };
        }

        /// <summary>Joue un son dans le micro. Un son déjà en cours est relancé depuis le début
        /// (même comportement que l'aperçu local), les autres continuent.</summary>
        public void Play(string soundId, string filePath, float volume, TimeSpan? trimStart = null, TimeSpan? trimEnd = null)
        {
            var reader = AudioReaderFactory.OpenForPlayback(filePath, out var setVolume);
            setVolume(volume);
            WaveStream playable = trimStart is { } start && trimEnd is { } end && end > start
                ? new TrimmedWaveStream(reader, start, end)
                : reader;
            var stream = new SingleShotWaveStream(playable);
            AddInput(new Input(soundId, ToMixFormat(stream.ToSampleProvider()), stream));
        }

        /// <summary>Bip court (880 Hz, ~1,5 s) pour vérifier que l'effet est bien actif sur le micro.</summary>
        public void PlayTestTone()
        {
            var tone = new SignalGenerator(MicFeedRing.SampleRate, 1)
            {
                Type = SignalGeneratorType.Sin,
                Frequency = 880,
                Gain = 0.25,
            }.Take(TimeSpan.FromSeconds(1.5));
            AddInput(new Input(TestToneId, tone, null));
        }

        public void StopSound(string soundId)
        {
            Input? input;
            lock (_lock)
            {
                if (!_inputs.Remove(soundId, out input)) return;
            }
            _mixer.RemoveMixerInput(input);
            input.Dispose();
            SoundEnded?.Invoke(soundId);
        }

        public void StopAll()
        {
            List<string> ids;
            lock (_lock) ids = new List<string>(_inputs.Keys);
            foreach (var id in ids) StopSound(id);
        }

        /// <summary>État de l'effet vu depuis WaseBoard (lecture seule, sans rien démarrer).</summary>
        public static MicFeedStatus ReadStatus()
        {
            using var ring = MicFeedRing.TryOpen();
            if (ring is null) return new MicFeedStatus(false, false, 0, 0);

            var listening = Environment.TickCount64 - ring.ReadTick < 1500;
            return new MicFeedStatus(true, listening, ring.ReaderRate, ring.ReaderChannels);
        }

        private void AddInput(Input input)
        {
            Input? previous;
            lock (_lock)
            {
                _inputs.Remove(input.SoundId, out previous);
                _inputs[input.SoundId] = input;
            }
            if (previous is not null)
            {
                _mixer.RemoveMixerInput(previous);
                previous.Dispose();
            }
            _mixer.AddMixerInput(input);
            EnsureThread();
            _wake.Set();
        }

        private bool HasInputs
        {
            get { lock (_lock) return _inputs.Count > 0; }
        }

        /// <summary>48 kHz mono float, quel que soit le fichier d'origine.</summary>
        private static ISampleProvider ToMixFormat(ISampleProvider source)
        {
            var channels = source.WaveFormat.Channels;
            if (channels == 2)
                source = new StereoToMonoSampleProvider(source) { LeftVolume = 0.5f, RightVolume = 0.5f };
            else if (channels > 2)
                source = new DownmixToMonoSampleProvider(source);

            if (source.WaveFormat.SampleRate != MicFeedRing.SampleRate)
                source = new WdlResamplingSampleProvider(source, MicFeedRing.SampleRate);
            return source;
        }

        private void EnsureThread()
        {
            if (_thread is not null) return;
            lock (_lock)
            {
                if (_thread is not null || _disposed) return;
                _thread = new Thread(Run)
                {
                    IsBackground = true,
                    Name = "WaseBoard micro (écriture)",
                    Priority = ThreadPriority.AboveNormal,
                };
                _thread.Start();
            }
        }

        private void Run()
        {
            var buffer = new float[MicFeedRing.SampleRate / 2];
            using var sleeper = new PreciseSleeper();

            while (!_disposed)
            {
                if (!HasInputs)
                {
                    _wake.WaitOne();
                    continue;
                }
                RunSession(buffer, sleeper);
            }
        }

        /// <summary>Une session d'écriture : du premier son jusqu'à un peu de silence après le dernier
        /// (au moins l'avance, pour que les lecteurs ne gardent pas la fin d'un son en suspens).</summary>
        private void RunSession(float[] buffer, PreciseSleeper sleeper)
        {
            var clock = Stopwatch.StartNew();
            long produced = 0;
            long silenceStart = -1;
            long nextRingAttempt = 0;

            while (!_disposed)
            {
                // L'effet ne crée la mémoire partagée qu'une fois une application à l'écoute d'un
                // micro équipé : on retente régulièrement, sans bloquer la progression des sons.
                if (_ring is null && clock.ElapsedMilliseconds >= nextRingAttempt)
                {
                    _ring = MicFeedRing.TryOpen();
                    nextRingAttempt = clock.ElapsedMilliseconds + 500;
                }

                var due = (long)(clock.Elapsed.TotalSeconds * MicFeedRing.SampleRate) + MicFeedRing.LeadFrames;

                // Rattrapage après une longue pause (veille, gel) : on abandonne le retard plutôt que
                // de pousser d'un coup plusieurs secondes dans le micro.
                if (due - produced > MicFeedRing.SampleRate / 2)
                    produced = due - MicFeedRing.LeadFrames;

                var count = (int)Math.Min(due - produced, buffer.Length);
                if (count > 0)
                {
                    _mixer.Read(buffer, 0, count);
                    var gain = Volume;
                    if (gain != 1.0f)
                        for (var i = 0; i < count; i++) buffer[i] *= gain;

                    WriteToRing(buffer, count);
                    produced += count;
                }

                if (HasInputs)
                {
                    silenceStart = -1;
                }
                else
                {
                    if (silenceStart < 0) silenceStart = produced;
                    else if (produced - silenceStart >= MicFeedRing.LeadFrames * 2) break;
                }

                sleeper.Sleep(5);
            }
        }

        private void WriteToRing(float[] buffer, int count)
        {
            var ring = _ring;
            if (ring is null) return;
            try
            {
                if (!ring.IsCompatible || ring.IsOwnedByAnotherWriter()) return;
                ring.Heartbeat();
                ring.Write(buffer, count);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ObjectDisposedException)
            {
                _ring = null;
                ring.Dispose();
            }
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            _wake.Set();
            _thread?.Join(500);
            StopAll();
            _ring?.Dispose();
            _wake.Dispose();
        }

        /// <summary>Entrée du mixeur rattachée à un son, pour pouvoir la retrouver (arrêt, relance) et
        /// libérer le fichier une fois terminée.</summary>
        private sealed class Input : ISampleProvider, IDisposable
        {
            private readonly ISampleProvider _source;
            private readonly IDisposable? _owner;
            private int _disposed;

            public Input(string soundId, ISampleProvider source, IDisposable? owner)
            {
                SoundId = soundId;
                _source = source;
                _owner = owner;
            }

            public string SoundId { get; }
            public WaveFormat WaveFormat => _source.WaveFormat;
            public int Read(float[] buffer, int offset, int count) => _source.Read(buffer, offset, count);
            // Idempotent : la fin naturelle (MixerInputEnded) et un arrêt manuel peuvent se croiser.
            public void Dispose()
            {
                if (Interlocked.Exchange(ref _disposed, 1) == 0) _owner?.Dispose();
            }
        }

        /// <summary>Moyenne de tous les canaux (fichiers 5.1, etc. — rare pour un son de soundboard).</summary>
        private sealed class DownmixToMonoSampleProvider : ISampleProvider
        {
            private readonly ISampleProvider _source;
            private readonly int _channels;
            private float[] _scratch = Array.Empty<float>();

            public DownmixToMonoSampleProvider(ISampleProvider source)
            {
                _source = source;
                _channels = source.WaveFormat.Channels;
                WaveFormat = WaveFormat.CreateIeeeFloatWaveFormat(source.WaveFormat.SampleRate, 1);
            }

            public WaveFormat WaveFormat { get; }

            public int Read(float[] buffer, int offset, int count)
            {
                var needed = count * _channels;
                if (_scratch.Length < needed) _scratch = new float[needed];
                var read = _source.Read(_scratch, 0, needed) / _channels;
                for (var f = 0; f < read; f++)
                {
                    var sum = 0f;
                    for (var c = 0; c < _channels; c++) sum += _scratch[f * _channels + c];
                    buffer[offset + f] = sum / _channels;
                }
                return read;
            }
        }

        /// <summary>Attente de quelques millisecondes réellement précise (minuteur haute résolution,
        /// Windows 10 1803+), sans changer la résolution d'horloge de tout le système. Repli sur
        /// Thread.Sleep (~15 ms) : l'avance de 60 ms l'absorbe.</summary>
        private sealed class PreciseSleeper : IDisposable
        {
            private const uint CreateWaitableTimerHighResolution = 0x00000002;
            private const uint TimerAllAccess = 0x1F0003;
            private const uint Infinite = 0xFFFFFFFF;

            private readonly IntPtr _timer;

            public PreciseSleeper()
            {
                _timer = CreateWaitableTimerExW(IntPtr.Zero, null, CreateWaitableTimerHighResolution, TimerAllAccess);
            }

            public void Sleep(int milliseconds)
            {
                if (_timer != IntPtr.Zero)
                {
                    var dueTime = -milliseconds * 10_000L; // relatif, en unités de 100 ns
                    if (SetWaitableTimer(_timer, ref dueTime, 0, IntPtr.Zero, IntPtr.Zero, false))
                    {
                        WaitForSingleObject(_timer, Infinite);
                        return;
                    }
                }
                Thread.Sleep(milliseconds);
            }

            public void Dispose()
            {
                if (_timer != IntPtr.Zero) CloseHandle(_timer);
            }

            [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
            private static extern IntPtr CreateWaitableTimerExW(IntPtr lpTimerAttributes, string? lpTimerName, uint dwFlags, uint dwDesiredAccess);

            [DllImport("kernel32.dll", SetLastError = true)]
            [return: MarshalAs(UnmanagedType.Bool)]
            private static extern bool SetWaitableTimer(IntPtr hTimer, ref long pDueTime, int lPeriod, IntPtr pfnCompletionRoutine, IntPtr lpArgToCompletionRoutine, [MarshalAs(UnmanagedType.Bool)] bool fResume);

            [DllImport("kernel32.dll", SetLastError = true)]
            private static extern uint WaitForSingleObject(IntPtr hHandle, uint dwMilliseconds);

            [DllImport("kernel32.dll", SetLastError = true)]
            [return: MarshalAs(UnmanagedType.Bool)]
            private static extern bool CloseHandle(IntPtr hObject);
        }
    }

    /// <summary>EffectLoaded : la mémoire partagée existe (l'effet a été chargé au moins une fois depuis
    /// le démarrage du service audio). MicInUse : une application écoute en ce moment un micro équipé.</summary>
    public readonly record struct MicFeedStatus(bool EffectLoaded, bool MicInUse, uint SampleRate, uint Channels);
}

using System;
using System.Threading;
using System.Threading.Tasks;
using NAudio.CoreAudioApi;
using NAudio.CoreAudioApi.Interfaces;
using NAudio.Wave;

namespace WaseBoard.Services
{
    /// <summary>Un extrait du son du PC : PCM 16 bits entrelacé.</summary>
    public sealed record ClipSnapshot(byte[] Pcm, int SampleRate, int Channels)
    {
        public double Seconds => SampleRate > 0 && Channels > 0 ? (double)Pcm.Length / (SampleRate * Channels * 2) : 0;
    }

    /// <summary>
    /// Garde en mémoire les dernières secondes de ce que le PC joue (jeu, musique, voix des autres dans Discord…) pour en
    /// faire un son sur demande — « clip » à la manière d'un replay instantané. Capture « loopback » de la sortie par défaut
    /// (WASAPI, via NAudio) : on n'écoute PAS le micro.
    ///
    /// Vie privée : l'audio reste en mémoire vive (ClipRingBuffer), jamais sur le disque ; il ne sort de là que lorsque
    /// l'utilisateur demande un clip (<see cref="Snapshot"/>), puis il choisit lui-même ce qu'il garde. Le service ne tourne
    /// que si le réglage est activé, et l'interface montre un voyant tant qu'il capture.
    ///
    /// Robustesse : si la sortie par défaut change (casque branché…) ou que le périphérique disparaît, la capture repart
    /// seule, avec une attente croissante en cas d'échec répété.
    /// </summary>
    public sealed class LoopbackClipService : IDisposable, IMMNotificationClient
    {
        /// <summary>Durées proposées dans les réglages (secondes).</summary>
        public static readonly int[] SecondsChoices = { 10, 15, 30 };
        public const int DefaultSeconds = 15;

        public static int NormalizeSeconds(int seconds) => Array.IndexOf(SecondsChoices, seconds) >= 0 ? seconds : DefaultSeconds;

        private sealed class Session
        {
            public WasapiLoopbackCapture Capture = null!;
            public ClipRingBuffer Buffer = null!;
            public PcmSampleKind Kind;
            public int SourceChannels;
            public byte[] Scratch = Array.Empty<byte>();
            public volatile bool Retired;
        }

        private readonly object _gate = new();
        private Session? _session;
        private MMDeviceEnumerator? _enumerator;
        private CancellationTokenSource? _retryCts;
        private int _bufferSeconds = DefaultSeconds;
        private int _retryDelayMs = 2000;
        private bool _wanted;
        private bool _disposed;

        /// <summary>Vrai tant que la capture tourne réellement.</summary>
        public bool IsCapturing { get; private set; }

        /// <summary>Dernier problème rencontré (null si tout va bien) : sert à l'infobulle du bouton.</summary>
        public string? LastError { get; private set; }

        /// <summary>Levé (depuis un fil quelconque) quand <see cref="IsCapturing"/> ou <see cref="LastError"/> change.</summary>
        public event Action? StateChanged;

        /// <summary>Applique les réglages : démarre, arrête ou redémarre (si la durée change) selon le cas.</summary>
        public void Apply(bool enabled, int seconds)
        {
            seconds = NormalizeSeconds(seconds);
            lock (_gate)
            {
                if (_disposed) return;
                if (!enabled)
                {
                    StopLocked();
                    return;
                }

                if (_wanted && IsCapturing && seconds == _bufferSeconds) return;
                _wanted = true;
                _bufferSeconds = seconds;
                EnsureNotificationsLocked();
                StartCaptureLocked();
            }
        }

        public void Stop()
        {
            lock (_gate) StopLocked();
        }

        /// <summary>Les dernières <paramref name="seconds"/> secondes, jusqu'à maintenant ; null si la capture ne tourne pas.</summary>
        public ClipSnapshot? Snapshot(double seconds)
        {
            Session? session;
            lock (_gate) session = IsCapturing ? _session : null;
            if (session is null) return null;
            return new ClipSnapshot(session.Buffer.Snapshot(seconds), session.Buffer.SampleRate, session.Buffer.Channels);
        }

        private void StopLocked()
        {
            _wanted = false;
            CancelRetryLocked();
            RetireSessionLocked();
            SetStateLocked(false, null);
        }

        private void StartCaptureLocked()
        {
            CancelRetryLocked();
            RetireSessionLocked();
            try
            {
                var capture = new WasapiLoopbackCapture(); // sortie par défaut au moment de l'appel
                var format = capture.WaveFormat;
                var kind = DetectKind(format) ?? throw new NotSupportedException($"Format audio non géré ({format.Encoding}, {format.BitsPerSample} bits).");

                var session = new Session
                {
                    Capture = capture,
                    Kind = kind,
                    SourceChannels = format.Channels,
                    Buffer = new ClipRingBuffer(format.SampleRate, PcmTools.OutputChannels(format.Channels), _bufferSeconds)
                };
                capture.DataAvailable += (_, e) => OnData(session, e.Buffer, e.BytesRecorded);
                capture.RecordingStopped += (_, e) => OnStopped(session, e.Exception);
                capture.StartRecording();

                _session = session;
                _retryDelayMs = 2000;
                SetStateLocked(true, null);
            }
            catch (Exception ex)
            {
                SetStateLocked(false, ex.Message);
                ScheduleRetryLocked();
            }
        }

        private static readonly Guid IeeeFloatSubFormat = new("00000003-0000-0010-8000-00aa00389b71");

        private static PcmSampleKind? DetectKind(WaveFormat format)
        {
            var encoding = format.Encoding;
            if (encoding == WaveFormatEncoding.Extensible && format is WaveFormatExtensible extensible)
            {
                // Seul le sous-format compte : flottant (KSDATAFORMAT_SUBTYPE_IEEE_FLOAT) ou entier.
                encoding = extensible.SubFormat == IeeeFloatSubFormat ? WaveFormatEncoding.IeeeFloat : WaveFormatEncoding.Pcm;
            }

            return (encoding, format.BitsPerSample) switch
            {
                (WaveFormatEncoding.IeeeFloat, 32) => PcmSampleKind.Float32,
                (WaveFormatEncoding.Pcm, 16) => PcmSampleKind.Int16,
                (WaveFormatEncoding.Pcm, 24) => PcmSampleKind.Int24,
                (WaveFormatEncoding.Pcm, 32) => PcmSampleKind.Int32,
                _ => null
            };
        }

        // Fil de capture : le plus court possible (conversion + copie en mémoire).
        private static void OnData(Session session, byte[] data, int count)
        {
            if (session.Retired || count <= 0) return;

            var needed = PcmTools.OutputSize(count, session.Kind, session.SourceChannels);
            if (session.Scratch.Length < needed) session.Scratch = new byte[needed];
            var written = PcmTools.ConvertToPcm16(data.AsSpan(0, count), session.Kind, session.SourceChannels, session.Scratch);
            session.Buffer.Append(session.Scratch.AsSpan(0, written));
        }

        private void OnStopped(Session session, Exception? error)
        {
            // Arrêt voulu : on ne touche PAS au verrou. Celui qui a demandé l'arrêt attend peut-être la fin de ce fil
            // (StopRecording) tout en le tenant — le prendre ici serait un blocage mutuel.
            if (session.Retired) return;

            lock (_gate)
            {
                if (session.Retired || !ReferenceEquals(session, _session)) return;

                RetireSessionLocked();
                SetStateLocked(false, error?.Message ?? "capture interrompue");
                if (_wanted) ScheduleRetryLocked();
            }
        }

        private void RetireSessionLocked()
        {
            var session = _session;
            _session = null;
            if (session is null) return;

            session.Retired = true;
            // L'arrêt d'une capture WASAPI peut attendre son fil : hors du verrou et hors du fil d'interface.
            Task.Run(() =>
            {
                try { session.Capture.StopRecording(); } catch { /* périphérique déjà parti */ }
                try { session.Capture.Dispose(); } catch { /* idem */ }
            });
        }

        private void ScheduleRetryLocked()
        {
            if (!_wanted || _disposed) return;

            var cts = new CancellationTokenSource();
            _retryCts = cts;
            var delay = _retryDelayMs;
            _retryDelayMs = Math.Min(_retryDelayMs * 2, 30000);
            _ = Task.Delay(delay, cts.Token).ContinueWith(t =>
            {
                if (t.IsCanceled) return;
                lock (_gate)
                {
                    if (_wanted && !_disposed && !IsCapturing && ReferenceEquals(_retryCts, cts)) StartCaptureLocked();
                }
            }, TaskScheduler.Default);
        }

        private void CancelRetryLocked()
        {
            _retryCts?.Cancel();
            _retryCts = null;
        }

        private void SetStateLocked(bool capturing, string? error)
        {
            var changed = IsCapturing != capturing || LastError != error;
            IsCapturing = capturing;
            LastError = error;
            if (changed) StateChanged?.Invoke();
        }

        // ---------- Changement de sortie audio par défaut ----------

        private void EnsureNotificationsLocked()
        {
            if (_enumerator is not null) return;
            try
            {
                _enumerator = new MMDeviceEnumerator();
                _enumerator.RegisterEndpointNotificationCallback(this);
            }
            catch
            {
                // Sans notification, la capture suit seulement les erreurs (voir OnStopped) : pas bloquant.
            }
        }

        public void OnDefaultDeviceChanged(DataFlow flow, Role role, string defaultDeviceId)
        {
            if (flow != DataFlow.Render || role != Role.Console) return;

            // Léger délai : Windows envoie plusieurs notifications d'affilée lors d'un branchement.
            _ = Task.Delay(500).ContinueWith(_ =>
            {
                lock (_gate)
                {
                    if (_wanted && !_disposed) StartCaptureLocked();
                }
            }, TaskScheduler.Default);
        }

        public void OnDeviceStateChanged(string deviceId, DeviceState newState) { }
        public void OnDeviceAdded(string pwstrDeviceId) { }
        public void OnDeviceRemoved(string deviceId) { }
        public void OnPropertyValueChanged(string pwstrDeviceId, PropertyKey key) { }

        public void Dispose()
        {
            Session? session;
            lock (_gate)
            {
                if (_disposed) return;
                _disposed = true;
                _wanted = false;
                CancelRetryLocked();

                session = _session;
                _session = null;
                IsCapturing = false;
                if (session is not null) session.Retired = true;

                try { _enumerator?.UnregisterEndpointNotificationCallback(this); } catch { }
                try { _enumerator?.Dispose(); } catch { }
                _enumerator = null;
            }

            // Hors du verrou : l'arrêt attend la fin du fil de capture.
            if (session is not null)
            {
                try { session.Capture.StopRecording(); } catch { }
                try { session.Capture.Dispose(); } catch { }
            }
        }
    }
}

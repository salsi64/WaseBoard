using System;

namespace WaseBoard.Services
{
    /// <summary>
    /// Mémoire circulaire des N dernières secondes d'un flux audio PCM 16 bits entrelacé (clip « les dernières secondes »).
    /// Rien n'est écrit sur le disque : tout vit ici, en RAM, et les plus anciens échantillons sont écrasés au fur et à mesure.
    ///
    /// Piège traité ici : la capture « loopback » de Windows ne livre RIEN pendant les silences (aucun paquet tant que rien
    /// ne joue). Sans précaution, « les 15 dernières secondes » couvriraient en réalité bien plus de temps réel, et le
    /// clip serait amputé de ses silences. On compare donc l'horloge à la quantité de son reçue : tout trou est comblé de
    /// silence, y compris le trou final entre le dernier paquet et l'instant où l'on demande le clip.
    ///
    /// Sans dépendance à Windows ni à NAudio (l'horloge est injectable) : testé tel quel par WaseBoard.Tests.
    /// </summary>
    public sealed class ClipRingBuffer
    {
        public const int BytesPerSample = 2;

        /// <summary>En dessous de ce retard, on ne comble pas : les paquets arrivent par rafales (fil occupé, ramasse-miettes),
        /// pas de vraie pause. Au-delà, c'est un silence réel.</summary>
        public const double GapToleranceMs = 250;

        private readonly object _lock = new();
        private readonly byte[] _ring;
        private readonly Func<double> _clockMs;
        private int _writePos;
        private int _filled;
        private double _lastEndMs;

        public int SampleRate { get; }
        public int Channels { get; }
        public int BlockAlign => Channels * BytesPerSample;
        public int BytesPerSecond => SampleRate * BlockAlign;
        public double CapacitySeconds => (double)_ring.Length / BytesPerSecond;

        /// <param name="clockMs">Horloge en millisecondes (par défaut : monotone du système). Injectable pour les tests.</param>
        public ClipRingBuffer(int sampleRate, int channels, double capacitySeconds, Func<double>? clockMs = null)
        {
            if (sampleRate <= 0) throw new ArgumentOutOfRangeException(nameof(sampleRate));
            if (channels is < 1 or > 2) throw new ArgumentOutOfRangeException(nameof(channels), "Mono or stereo.");
            if (capacitySeconds <= 0) throw new ArgumentOutOfRangeException(nameof(capacitySeconds));

            SampleRate = sampleRate;
            Channels = channels;
            _clockMs = clockMs ?? (() => System.Diagnostics.Stopwatch.GetTimestamp() * 1000.0 / System.Diagnostics.Stopwatch.Frequency);
            _ring = new byte[(int)Math.Ceiling(sampleRate * capacitySeconds) * BlockAlign];
            _lastEndMs = _clockMs();
        }

        /// <summary>Octets actuellement gardés (au plus la capacité).</summary>
        public int BufferedBytes
        {
            get { lock (_lock) return _filled; }
        }

        public double BufferedSeconds => (double)BufferedBytes / BytesPerSecond;

        /// <summary>Ajoute un paquet qui vient d'arriver (il couvre les instants juste avant « maintenant »). Un reste qui
        /// ne tombe pas sur un échantillon entier est ignoré.</summary>
        public void Append(ReadOnlySpan<byte> pcm16)
        {
            var usable = pcm16.Length - pcm16.Length % BlockAlign;
            if (usable <= 0) return;

            lock (_lock)
            {
                var now = _clockMs();
                var startMs = now - usable * 1000.0 / BytesPerSecond;
                FillSilenceLocked(startMs - _lastEndMs);
                WriteLocked(pcm16[..usable]);
                _lastEndMs = now;
            }
        }

        /// <summary>Les <paramref name="seconds"/> dernières secondes, de la plus ancienne à la plus récente, jusqu'à
        /// « maintenant » (un silence final est ajouté si plus rien n'est arrivé depuis). Plus court si moins est gardé.</summary>
        public byte[] Snapshot(double seconds)
        {
            lock (_lock)
            {
                var now = _clockMs();
                FillSilenceLocked(now - _lastEndMs);
                _lastEndMs = Math.Max(_lastEndMs, now);

                var wanted = (int)Math.Min((long)_ring.Length, (long)Math.Round(Math.Max(0, seconds) * SampleRate) * BlockAlign);
                var count = Math.Min(_filled, wanted);
                count -= count % BlockAlign;

                var result = new byte[count];
                if (count == 0) return result;

                var start = (_writePos - count + _ring.Length) % _ring.Length;
                var firstPart = Math.Min(count, _ring.Length - start);
                Buffer.BlockCopy(_ring, start, result, 0, firstPart);
                if (firstPart < count) Buffer.BlockCopy(_ring, 0, result, firstPart, count - firstPart);
                return result;
            }
        }

        /// <summary>Vide la mémoire (changement de périphérique, arrêt).</summary>
        public void Clear()
        {
            lock (_lock)
            {
                _writePos = 0;
                _filled = 0;
                _lastEndMs = _clockMs();
            }
        }

        private void FillSilenceLocked(double gapMs)
        {
            if (gapMs <= GapToleranceMs) return;

            var bytes = (long)(gapMs * BytesPerSecond / 1000.0);
            bytes -= bytes % BlockAlign;
            if (bytes <= 0) return;

            // Un trou plus long que la mémoire la remplit entièrement de silence : inutile d'écrire davantage.
            var count = (int)Math.Min(bytes, _ring.Length);
            var zeros = new byte[Math.Min(count, 65536)];
            var remaining = count;
            while (remaining > 0)
            {
                var chunk = Math.Min(remaining, zeros.Length);
                WriteLocked(zeros.AsSpan(0, chunk));
                remaining -= chunk;
            }
        }

        private void WriteLocked(ReadOnlySpan<byte> data)
        {
            if (data.Length >= _ring.Length)
            {
                // Plus qu'il n'en faut : seule la fin compte.
                data[^_ring.Length..].CopyTo(_ring);
                _writePos = 0;
                _filled = _ring.Length;
                return;
            }

            var firstPart = Math.Min(data.Length, _ring.Length - _writePos);
            data[..firstPart].CopyTo(_ring.AsSpan(_writePos));
            if (firstPart < data.Length) data[firstPart..].CopyTo(_ring);

            _writePos = (_writePos + data.Length) % _ring.Length;
            _filled = Math.Min(_ring.Length, _filled + data.Length);
        }
    }
}

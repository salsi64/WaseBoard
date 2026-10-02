using System;
using NAudio.Wave;

namespace WaseBoard.Services
{
    /// <summary>
    /// Ne délivre que la portion [début, fin] d'un flux audio, sans jamais modifier le fichier source :
    /// c'est ce qui rend la découpe d'un son non destructive (le fichier complet reste intact, la portion
    /// gardée n'est qu'une paire de repères). Le début est atteint par lecture séquentielle (jamais de
    /// seek) : pour les formats à débit variable (MP3 VBR, Ogg), le seek de NAudio est approximatif, alors
    /// que lire et jeter les premiers octets est exact. Sert à l'aperçu local ET à l'aperçu de la fenêtre
    /// de découpe — ils correspondent donc pile à ce que le serveur joue (ffmpeg -ss/-t).
    /// </summary>
    internal sealed class TrimmedWaveStream : WaveStream
    {
        private readonly WaveStream _source;
        private readonly long _skipBytes;
        private readonly long _keepBytes;
        private readonly byte[] _discard = new byte[8192];
        private long _skipped;
        private long _delivered;

        public TrimmedWaveStream(WaveStream source, TimeSpan start, TimeSpan end)
        {
            _source = source;
            var bytesPerSecond = source.WaveFormat.AverageBytesPerSecond;
            var blockAlign = Math.Max(1, source.WaveFormat.BlockAlign);

            // Alignement sur une frame audio complète : sinon la lecture pourrait démarrer au milieu
            // d'une frame stéréo et inverser/corrompre les canaux.
            _skipBytes = AlignDown((long)(Math.Max(0, start.TotalSeconds) * bytesPerSecond), blockAlign);
            _keepBytes = AlignDown((long)(Math.Max(0, (end - start).TotalSeconds) * bytesPerSecond), blockAlign);
        }

        private static long AlignDown(long bytes, int blockAlign) => bytes - bytes % blockAlign;

        public override WaveFormat WaveFormat => _source.WaveFormat;
        public override long Length => _keepBytes;

        public override long Position
        {
            get => _delivered;
            set => throw new NotSupportedException("Flux découpé : lecture séquentielle uniquement.");
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            while (_skipped < _skipBytes)
            {
                var toSkip = (int)Math.Min(_discard.Length, _skipBytes - _skipped);
                var read = _source.Read(_discard, 0, toSkip);
                if (read <= 0) return 0; // le fichier se termine avant le début demandé
                _skipped += read;
            }

            if (_delivered >= _keepBytes) return 0;

            var allowed = (int)Math.Min(count, _keepBytes - _delivered);
            var delivered = _source.Read(buffer, offset, allowed);
            _delivered += delivered;
            return delivered;
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) _source.Dispose();
            base.Dispose(disposing);
        }
    }
}

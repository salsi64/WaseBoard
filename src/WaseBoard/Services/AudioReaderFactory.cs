using System;
using System.Collections.Generic;
using System.IO;
using Concentus;
using Concentus.Oggfile;
using Concentus.Structs;
using NAudio.Vorbis;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace WaseBoard.Services
{
    /// <summary>Ouvre un fichier audio de façon uniforme (lecture, waveform, export). NAudio ne
    /// décode pas nativement l'Ogg Vorbis/Opus : NAudio.Vorbis et Concentus (décodeurs C# purs)
    /// évitent une dépendance à un codec Windows Media Foundation.</summary>
    public static class AudioReaderFactory
    {
        /// <summary>Ouvre le fichier et retourne un WaveStream avec volume ajustable.</summary>
        public static WaveStream OpenForPlayback(string filePath, out Action<float> setVolume)
        {
            if (IsOgg(filePath))
            {
                WaveStream source = IsOpus(filePath, out var channels)
                    ? DecodeOpusToRawStream(filePath, channels)
                    : new VorbisWaveReader(filePath);

                var volumeProvider = new VolumeSampleProvider(source.ToSampleProvider());
                var waveProvider = volumeProvider.ToWaveProvider();
                var wrapped = new SampleProviderWaveStream(source, waveProvider);
                setVolume = v => volumeProvider.Volume = Math.Clamp(v, 0f, 2f);
                return wrapped;
            }

            var reader = new AudioFileReader(filePath);
            setVolume = v => reader.Volume = Math.Clamp(v, 0f, 2f);
            return reader;
        }

        /// <summary>Ouvre le fichier pour une lecture séquentielle brute (waveform / export), sans notion de volume.</summary>
        public static WaveStream OpenForDecode(string filePath)
        {
            if (IsOgg(filePath))
            {
                return IsOpus(filePath, out var channels)
                    ? DecodeOpusToRawStream(filePath, channels)
                    : new VorbisWaveReader(filePath);
            }

            return new AudioFileReader(filePath);
        }

        private static bool IsOgg(string filePath) =>
            string.Equals(Path.GetExtension(filePath), ".ogg", StringComparison.OrdinalIgnoreCase);

        /// <summary>
        /// Détecte si un fichier .ogg utilise le codec Opus (plutôt que Vorbis) en cherchant la
        /// signature "OpusHead" dans l'en-tête, et lit au passage son nombre de canaux (RFC 7845 :
        /// l'octet juste après "OpusHead" + le numéro de version correspond au nombre de canaux).
        /// </summary>
        private static bool IsOpus(string filePath, out int channels)
        {
            channels = 2;
            try
            {
                using var fs = File.OpenRead(filePath);
                var buffer = new byte[4096];
                int read = fs.Read(buffer, 0, buffer.Length);

                for (int i = 0; i < read - 9; i++)
                {
                    if (buffer[i] == 'O' && buffer[i + 1] == 'p' && buffer[i + 2] == 'u' && buffer[i + 3] == 's' &&
                        buffer[i + 4] == 'H' && buffer[i + 5] == 'e' && buffer[i + 6] == 'a' && buffer[i + 7] == 'd')
                    {
                        channels = Math.Max(1, (int)buffer[i + 9]);
                        return true;
                    }
                }
            }
            catch { /* on laisse le décodeur Vorbis échouer normalement si la lecture d'en-tête échoue */ }
            return false;
        }

        /// <summary>
        /// Décode un fichier Ogg Opus intégralement en PCM 16 bits via Concentus (décodeur Opus
        /// géré, sans dépendance native) et l'expose comme un WaveStream classique. Les clips de
        /// soundboard étant courts, un décodage complet en mémoire en une fois est plus simple et
        /// plus robuste qu'un flux streamé, pour un coût négligeable.
        /// </summary>
        private static WaveStream DecodeOpusToRawStream(string filePath, int channels)
        {
            using var fileStream = File.OpenRead(filePath);
            var decoder = OpusCodecFactory.CreateDecoder(48000, channels);
            var oggIn = new OpusOggReadStream(decoder, fileStream);

            var pcm = new List<short>();
            while (oggIn.HasNextPacket)
            {
                var packet = oggIn.DecodeNextPacket();
                if (packet != null) pcm.AddRange(packet);
            }

            var bytes = new byte[pcm.Count * 2];
            Buffer.BlockCopy(pcm.ToArray(), 0, bytes, 0, bytes.Length);

            return new RawSourceWaveStream(new MemoryStream(bytes), new WaveFormat(48000, 16, channels));
        }

        /// <summary>
        /// Adapte un IWaveProvider (issu d'un pipeline ISampleProvider, ex: Vorbis/Opus + volume) en
        /// WaveStream exploitable par WasapiOut.Init, tout en gardant une référence au flux source
        /// pour le Dispose.
        /// </summary>
        private class SampleProviderWaveStream : WaveStream
        {
            private readonly WaveStream _source;
            private readonly IWaveProvider _provider;

            public SampleProviderWaveStream(WaveStream source, IWaveProvider provider)
            {
                _source = source;
                _provider = provider;
            }

            public override WaveFormat WaveFormat => _provider.WaveFormat;
            public override long Length => _source.Length;
            public override long Position
            {
                get => _source.Position;
                set => _source.Position = value;
            }

            public override int Read(byte[] buffer, int offset, int count) => _provider.Read(buffer, offset, count);

            protected override void Dispose(bool disposing)
            {
                if (disposing) _source.Dispose();
                base.Dispose(disposing);
            }
        }
    }
}

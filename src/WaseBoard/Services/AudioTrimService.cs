using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using NAudio.Wave;

namespace WaseBoard.Services
{
    public class WaveformData
    {
        /// <summary>Amplitude max par intervalle, normalisée entre 0 et 1.</summary>
        public float[] Peaks { get; init; } = Array.Empty<float>();
        public TimeSpan Duration { get; init; }
    }

    /// <summary>Cache disque d'une mini-waveform (JSON), invalidé par date de modification du fichier source.</summary>
    internal class MiniWaveformCache
    {
        public float[] Peaks { get; set; } = Array.Empty<float>();
        public DateTime SourceLastWriteUtc { get; set; }
    }

    /// <summary>
    /// Calcule une waveform simplifiée pour l'affichage, et permet d'exporter
    /// un segment précis d'un fichier audio vers un nouveau fichier WAV.
    /// </summary>
    public static class AudioTrimService
    {
        public static WaveformData ComputeWaveform(string filePath, int bucketCount = 400)
        {
            using var reader = AudioReaderFactory.OpenForDecode(filePath);

            int channels = reader.WaveFormat.Channels;
            int bytesPerSample = reader.WaveFormat.BitsPerSample / 8;

            // Le format réel varie selon le décodeur (AudioReaderFactory) : flottant 32 bits pour
            // wav/mp3/flac/Vorbis, PCM 16 bits pour l'Opus via Concentus — à détecter, pas supposer.
            bool isFloat32 = reader.WaveFormat.Encoding == WaveFormatEncoding.IeeeFloat && bytesPerSample == 4;
            bool isPcm16 = reader.WaveFormat.Encoding == WaveFormatEncoding.Pcm && bytesPerSample == 2;

            if (!isFloat32 && !isPcm16)
                throw new NotSupportedException(
                    $"Format audio non pris en charge pour l'aperçu waveform : {reader.WaveFormat.Encoding}, {reader.WaveFormat.BitsPerSample} bits.");

            double totalFrames = Math.Max(1, reader.Length / (double)(channels * bytesPerSample));

            var peaks = new float[bucketCount];
            var buffer = new byte[8192 - (8192 % (channels * bytesPerSample))]; // aligné sur une frame complète

            long framesReadSoFar = 0;
            int currentBucket = 0;
            float currentMax = 0f;

            int bytesRead;
            while ((bytesRead = reader.Read(buffer, 0, buffer.Length)) > 0)
            {
                int sampleCount = bytesRead / bytesPerSample;
                int framesInChunk = sampleCount / channels;

                for (int f = 0; f < framesInChunk; f++)
                {
                    for (int c = 0; c < channels; c++)
                    {
                        int byteOffset = (f * channels + c) * bytesPerSample;
                        float sample = isFloat32
                            ? BitConverter.ToSingle(buffer, byteOffset)
                            : BitConverter.ToInt16(buffer, byteOffset) / 32768f;

                        var abs = Math.Abs(sample);
                        if (abs > currentMax) currentMax = abs;
                    }
                    framesReadSoFar++;

                    // Tranches de temps strictement égales (division flottante, pas tronquée) pour
                    // que la position à l'écran corresponde exactement à celle utilisée à l'export.
                    int expectedBucket = (int)Math.Min(bucketCount - 1, framesReadSoFar * bucketCount / totalFrames);
                    if (expectedBucket > currentBucket)
                    {
                        peaks[currentBucket] = currentMax;
                        currentBucket = expectedBucket;
                        currentMax = 0f;
                    }
                }
            }
            peaks[currentBucket] = currentMax;

            var overallMax = peaks.Length > 0 ? peaks.Max() : 1f;
            if (overallMax < 0.0001f) overallMax = 1f;

            for (int i = 0; i < peaks.Length; i++)
                peaks[i] = Math.Clamp(peaks[i] / overallMax, 0f, 1f);

            return new WaveformData { Peaks = peaks, Duration = reader.TotalTime };
        }

        /// <summary>
        /// Mini-waveform pour affichage sur un bouton-son, avec cache disque invalidé par date de
        /// modification du fichier. Ne lève jamais (retourne null en cas d'échec) ; synchrone, à
        /// appeler en arrière-plan par l'appelant.
        /// </summary>
        public static float[]? GetOrComputeMiniWaveform(string soundId, string audioFilePath, string cacheDirectory, int bucketCount = 28)
        {
            Directory.CreateDirectory(cacheDirectory);
            var cachePath = Path.Combine(cacheDirectory, $"{soundId}.waveform.json");
            var sourceWriteTimeUtc = File.GetLastWriteTimeUtc(audioFilePath);

            if (File.Exists(cachePath))
            {
                try
                {
                    var cached = JsonSerializer.Deserialize<MiniWaveformCache>(File.ReadAllText(cachePath));
                    if (cached is not null && cached.SourceLastWriteUtc == sourceWriteTimeUtc)
                        return cached.Peaks;
                }
                catch { /* cache corrompu/format invalide : on recalcule ci-dessous */ }
            }

            try
            {
                var waveform = ComputeWaveform(audioFilePath, bucketCount);
                var cache = new MiniWaveformCache { Peaks = waveform.Peaks, SourceLastWriteUtc = sourceWriteTimeUtc };
                File.WriteAllText(cachePath, JsonSerializer.Serialize(cache));
                return waveform.Peaks;
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// Exporte le segment [start, end] du fichier source vers un nouveau fichier WAV temporaire.
        /// Retourne le chemin du fichier créé (à supprimer par l'appelant une fois copié dans la bibliothèque).
        /// </summary>
        public static string ExportTrimmedWav(string sourceFilePath, TimeSpan start, TimeSpan end)
        {
            using var reader = AudioReaderFactory.OpenForDecode(sourceFilePath);

            start = TimeSpan.FromSeconds(Math.Clamp(start.TotalSeconds, 0, reader.TotalTime.TotalSeconds));
            end = TimeSpan.FromSeconds(Math.Clamp(end.TotalSeconds, 0, reader.TotalTime.TotalSeconds));
            if (end <= start) end = reader.TotalTime;

            var outputPath = Path.Combine(Path.GetTempPath(), $"waseboard_trim_{Guid.NewGuid()}.wav");
            using var writer = new WaveFileWriter(outputPath, reader.WaveFormat);

            long bytesToSkip = (long)(start.TotalSeconds * reader.WaveFormat.AverageBytesPerSecond);
            long bytesToKeep = (long)((end - start).TotalSeconds * reader.WaveFormat.AverageBytesPerSecond);

            // Alignement sur une frame audio complète (BlockAlign) : sinon l'écriture peut démarrer
            // au milieu d'une frame stéréo et corrompre les canaux.
            int blockAlign = Math.Max(1, reader.WaveFormat.BlockAlign);
            bytesToSkip -= bytesToSkip % blockAlign;
            bytesToKeep -= bytesToKeep % blockAlign;

            // Lecture séquentielle depuis le début, jamais de seek : pour les formats à débit
            // variable (MP3 VBR, Ogg), le seek de NAudio est approximatif.
            var buffer = new byte[8192];
            long bytesReadTotal = 0;
            int bytesRead;

            while (bytesReadTotal < bytesToSkip + bytesToKeep &&
                   (bytesRead = reader.Read(buffer, 0, buffer.Length)) > 0)
            {
                long chunkStart = bytesReadTotal;
                long writeFrom = Math.Max(0, bytesToSkip - chunkStart);
                long writeTo = Math.Min(bytesRead, bytesToSkip + bytesToKeep - chunkStart);

                if (writeTo > writeFrom)
                    writer.Write(buffer, (int)writeFrom, (int)(writeTo - writeFrom));

                bytesReadTotal += bytesRead;
            }

            return outputPath;
        }
    }
}

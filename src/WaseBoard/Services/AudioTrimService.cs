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

            // IMPORTANT : selon le décodeur utilisé en amont (AudioReaderFactory), le format réel
            // varie — flottant 32 bits pour wav/mp3/flac/Vorbis, mais PCM 16 bits pour l'Opus décodé
            // via Concentus. Le code lisait auparavant TOUJOURS les échantillons comme des flottants
            // 32 bits (division fixe par 4), ce qui produisait une waveform totalement aberrante sur
            // les fichiers Opus (mauvaise interprétation des octets). On détecte maintenant le
            // format réel et on lit chaque échantillon en conséquence.
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

                    // IMPORTANT : chaque bucket représente une tranche de temps STRICTEMENT égale
                    // (calculée en division flottante, pas tronquée), pour que la position à l'écran
                    // corresponde toujours exactement à la même position temporelle lors de l'export —
                    // avec des tranches de taille inégale (dernière tranche plus courte), le mappage
                    // pixel → temps dérivait progressivement, plus fortement vers la droite de la
                    // waveform, ce qui rendait la découpe du côté droit peu fiable.
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
        /// Mini-waveform (peu de buckets) pour affichage direct sur un bouton-son, avec cache disque
        /// (%AppData%\WaseBoard\Cache\{id}.waveform.json) invalidé par date de modification du
        /// fichier audio local — évite de rescanner tout le catalogue à chaque lancement. Ne lève
        /// jamais : un fichier illisible ou un format non pris en charge se traduit juste par
        /// l'absence de waveform (pas de plantage), synchrone comme ComputeWaveform (à appeler en
        /// arrière-plan par l'appelant, voir MainWindow.PrecomputeWaveformsAsync).
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

            // IMPORTANT : on aligne les deux valeurs sur une frame audio complète (BlockAlign =
            // tous les canaux d'un même instant, ex: gauche+droite en stéréo). Sans ça, dès que le
            // marqueur de gauche est déplacé, l'écriture démarre au milieu d'une frame stéréo, ce
            // qui inverse/corrompt les canaux et peut rendre le fichier exporté illisible — c'est
            // ce qui cassait l'aperçu (et la découpe en général) dès qu'on bougeait le côté gauche.
            int blockAlign = Math.Max(1, reader.WaveFormat.BlockAlign);
            bytesToSkip -= bytesToSkip % blockAlign;
            bytesToKeep -= bytesToKeep % blockAlign;

            // Lecture strictement séquentielle depuis le tout début du fichier, jamais de seek
            // (reader.CurrentTime / reader.Position) : pour les formats à débit variable (MP3 VBR,
            // Ogg), le seek de NAudio est approximatif et peut atterrir sur le mauvais octet. En
            // relisant depuis le début et en ignorant simplement les octets avant "start", l'export
            // utilise exactement le même chemin de décodage que le calcul de la waveform.
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

using System;
using System.Buffers.Binary;
using System.IO;

namespace WaseBoard.Services
{
    /// <summary>Formats d'échantillons que la capture du son du PC peut livrer (mode partagé de Windows : presque toujours
    /// du flottant 32 bits, parfois du PCM 16/24/32 bits).</summary>
    public enum PcmSampleKind { Int16, Int24, Int32, Float32 }

    /// <summary>
    /// Conversions audio du clip « les dernières secondes » : n'importe quel PCM livré par Windows → PCM 16 bits stéréo (ou
    /// mono), niveau crête, et écriture d'un fichier WAV. Pur calcul, sans Windows : testé par WaseBoard.Tests.
    /// </summary>
    public static class PcmTools
    {
        public static int BytesPerSample(PcmSampleKind kind) => kind switch
        {
            PcmSampleKind.Int16 => 2,
            PcmSampleKind.Int24 => 3,
            _ => 4
        };

        /// <summary>Un clip est au plus stéréo : les enceintes 5.1/7.1 sont ramenées à deux voies.</summary>
        public static int OutputChannels(int inputChannels) => Math.Min(Math.Max(inputChannels, 1), 2);

        /// <summary>Taille (octets) du résultat de <see cref="ConvertToPcm16"/> pour <paramref name="sourceBytes"/> octets lus.</summary>
        public static int OutputSize(int sourceBytes, PcmSampleKind kind, int channels)
        {
            var frames = sourceBytes / (BytesPerSample(kind) * channels);
            return frames * OutputChannels(channels) * 2;
        }

        /// <summary>
        /// Convertit des échantillons entrelacés en PCM 16 bits (mono ou stéréo). Au-delà de deux voies, mélange vers
        /// gauche/droite avec l'ordre habituel de Windows (avant G/D, centre, basses, arrière, côtés) : le centre (dialogues
        /// des jeux) est conservé à −3 dB, les arrières à −6 dB, les basses sont laissées de côté. Écrête sans saturer.
        /// Retourne le nombre d'octets écrits dans <paramref name="destination"/> (de taille ≥ <see cref="OutputSize"/>).
        /// </summary>
        public static int ConvertToPcm16(ReadOnlySpan<byte> source, PcmSampleKind kind, int channels, Span<byte> destination)
        {
            if (channels < 1) throw new ArgumentOutOfRangeException(nameof(channels));

            var bytesPerSample = BytesPerSample(kind);
            var frames = source.Length / (bytesPerSample * channels);
            var outChannels = OutputChannels(channels);
            if (destination.Length < frames * outChannels * 2) throw new ArgumentException("Destination trop petite.", nameof(destination));

            Span<float> s = stackalloc float[Math.Max(channels, 8)];
            var written = 0;
            for (var frame = 0; frame < frames; frame++)
            {
                var offset = frame * bytesPerSample * channels;
                for (var c = 0; c < channels; c++) s[c] = ReadSample(source[(offset + c * bytesPerSample)..], kind);

                float left = s[0], right;
                if (channels == 1)
                {
                    right = left;
                }
                else
                {
                    right = s[1];
                    switch (channels)
                    {
                        case 3: // avant G/D + centre
                            left += 0.707f * s[2];
                            right += 0.707f * s[2];
                            break;
                        case 4: // quadriphonie : avant G/D + arrière G/D
                            left += 0.5f * s[2];
                            right += 0.5f * s[3];
                            break;
                        case 5: // 5.0 : avant G/D, centre, arrière G/D
                            left += 0.707f * s[2] + 0.5f * s[3];
                            right += 0.707f * s[2] + 0.5f * s[4];
                            break;
                        case >= 6: // 5.1 / 7.1 : avant G/D, centre, basses, arrière G/D, [côtés G/D]
                            left += 0.707f * s[2] + 0.5f * s[4];
                            right += 0.707f * s[2] + 0.5f * s[5];
                            if (channels >= 8)
                            {
                                left += 0.5f * s[6];
                                right += 0.5f * s[7];
                            }
                            break;
                    }
                }

                BinaryPrimitives.WriteInt16LittleEndian(destination[written..], ToInt16(left));
                written += 2;
                if (outChannels == 2)
                {
                    BinaryPrimitives.WriteInt16LittleEndian(destination[written..], ToInt16(right));
                    written += 2;
                }
            }
            return written;
        }

        private static float ReadSample(ReadOnlySpan<byte> data, PcmSampleKind kind) => kind switch
        {
            PcmSampleKind.Int16 => BinaryPrimitives.ReadInt16LittleEndian(data) / 32768f,
            PcmSampleKind.Int24 => (((data[0] | (data[1] << 8) | (data[2] << 16)) << 8) >> 8) / 8388608f, // extension du signe
            PcmSampleKind.Int32 => BinaryPrimitives.ReadInt32LittleEndian(data) / 2147483648f,
            _ => BinaryPrimitives.ReadSingleLittleEndian(data)
        };

        private static short ToInt16(float value)
        {
            if (float.IsNaN(value)) return 0;
            return (short)Math.Round(Math.Clamp(value, -1f, 1f) * 32767f);
        }

        /// <summary>Niveau crête (0 à 1) d'un PCM 16 bits : sert à reconnaître un clip entièrement silencieux.</summary>
        public static double PeakLevel(ReadOnlySpan<byte> pcm16)
        {
            var peak = 0;
            for (var i = 0; i + 1 < pcm16.Length; i += 2)
            {
                var v = Math.Abs((int)BinaryPrimitives.ReadInt16LittleEndian(pcm16[i..]));
                if (v > peak) peak = v;
            }
            return peak / 32768.0;
        }

        /// <summary>Écrit un fichier WAV PCM 16 bits (en-tête de 44 octets, puis les échantillons).</summary>
        public static void WriteWavFile(string path, ReadOnlySpan<byte> pcm16, int sampleRate, int channels)
        {
            using var stream = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None);
            Span<byte> header = stackalloc byte[44];
            WriteWavHeader(header, pcm16.Length, sampleRate, channels);
            stream.Write(header);
            stream.Write(pcm16);
        }

        public static void WriteWavHeader(Span<byte> header, int dataBytes, int sampleRate, int channels)
        {
            if (header.Length < 44) throw new ArgumentException("L'en-tête WAV fait 44 octets.", nameof(header));

            "RIFF"u8.CopyTo(header);
            BinaryPrimitives.WriteInt32LittleEndian(header[4..], 36 + dataBytes);
            "WAVE"u8.CopyTo(header[8..]);
            "fmt "u8.CopyTo(header[12..]);
            BinaryPrimitives.WriteInt32LittleEndian(header[16..], 16);
            BinaryPrimitives.WriteInt16LittleEndian(header[20..], 1);                       // PCM
            BinaryPrimitives.WriteInt16LittleEndian(header[22..], (short)channels);
            BinaryPrimitives.WriteInt32LittleEndian(header[24..], sampleRate);
            BinaryPrimitives.WriteInt32LittleEndian(header[28..], sampleRate * channels * 2); // octets par seconde
            BinaryPrimitives.WriteInt16LittleEndian(header[32..], (short)(channels * 2));     // octets par trame
            BinaryPrimitives.WriteInt16LittleEndian(header[34..], 16);                        // bits par échantillon
            "data"u8.CopyTo(header[36..]);
            BinaryPrimitives.WriteInt32LittleEndian(header[40..], dataBytes);
        }
    }
}

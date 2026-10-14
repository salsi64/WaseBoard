using System;
using System.IO;

namespace WaseBoard.Services
{
    /// <summary>
    /// Génère en mémoire un petit signal sonore (un ou deux bips) au format WAV : confirmation audible d'un raccourci
    /// clavier quand la fenêtre n'est pas visible (en plein jeu, le message à l'écran ne se voit pas). Aucune dépendance
    /// à Windows : le fichier produit est joué par System.Media.SoundPlayer côté application.
    /// </summary>
    public static class BeepTone
    {
        public const int SampleRate = 44100;

        /// <summary>Mode jeu : deux notes qui montent (« activé »).</summary>
        public static readonly int[] RisingTones = { 784, 1175 };

        /// <summary>Mode Discord : deux notes qui descendent (« retour à la normale »).</summary>
        public static readonly int[] FallingTones = { 659, 440 };

        /// <summary>Clip gardé : une seule note aiguë, distincte des deux notes de la bascule Jeu/Discord.</summary>
        public static readonly int[] ClipTones = { 1319 };

        /// <summary>WAV mono 16 bits : une note par fréquence, <paramref name="noteMs"/> chacune, avec un fondu d'entrée
        /// et de sortie de 8 ms pour éviter tout « clac » (un son tronqué net claque dans les haut-parleurs).</summary>
        public static byte[] BuildWav(int[] frequencies, int noteMs = 90, double volume = 0.35)
        {
            if (frequencies.Length == 0) throw new ArgumentException("Au moins une fréquence est attendue.", nameof(frequencies));
            if (noteMs < 20) throw new ArgumentOutOfRangeException(nameof(noteMs));

            var samplesPerNote = SampleRate * noteMs / 1000;
            var fade = Math.Min(SampleRate * 8 / 1000, samplesPerNote / 2);
            var totalSamples = samplesPerNote * frequencies.Length;

            using var stream = new MemoryStream(44 + totalSamples * 2);
            using var writer = new BinaryWriter(stream);
            writer.Write("RIFF"u8.ToArray());
            writer.Write(36 + totalSamples * 2);
            writer.Write("WAVE"u8.ToArray());
            writer.Write("fmt "u8.ToArray());
            writer.Write(16);                    // taille du bloc fmt
            writer.Write((short)1);              // PCM
            writer.Write((short)1);              // mono
            writer.Write(SampleRate);
            writer.Write(SampleRate * 2);        // octets par seconde
            writer.Write((short)2);              // octets par échantillon
            writer.Write((short)16);             // bits par échantillon
            writer.Write("data"u8.ToArray());
            writer.Write(totalSamples * 2);

            foreach (var frequency in frequencies)
            {
                for (var i = 0; i < samplesPerNote; i++)
                {
                    var envelope = i < fade ? (double)i / fade : i >= samplesPerNote - fade ? (double)(samplesPerNote - 1 - i) / fade : 1.0;
                    var value = Math.Sin(2 * Math.PI * frequency * i / SampleRate) * envelope * volume;
                    writer.Write((short)Math.Round(value * short.MaxValue));
                }
            }

            writer.Flush();
            return stream.ToArray();
        }
    }
}

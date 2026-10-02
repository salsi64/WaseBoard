using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Windows.Media.Imaging;

namespace WaseBoard
{
    /// <summary>
    /// Résout un emoji Unicode vers son image : Fluent Emoji 3D (Microsoft, licence MIT, servi par jsDelivr), le style
    /// 3D brillant et expressif de l'application ; à défaut — emoji absent de l'index (rare : 🅾️, variantes de teinte de
    /// peau...) — Twemoji, plat, qui couvre tout Unicode. Contourne aussi l'incapacité de WPF à afficher le format
    /// d'emoji couleur récent de Windows 11 (COLRv1) en glyphe texte, quelle que soit la police.
    /// </summary>
    public static class EmojiImageResolver
    {
        // Dépôt épinglé sur un commit : l'index embarqué (Assets/fluent-emoji-index.txt) correspond exactement à ce
        // contenu, un changement amont du dépôt ne peut donc ni casser une URL ni changer le style sans mise à jour ici.
        private const string FluentCommit = "1ffb34c752ecf5d402f04cfb4b392c77f57c54bc";
        private const string FluentBaseUrl = "https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@" + FluentCommit + "/assets/";
        private const string TwemojiBaseUrl = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@17.0.3/assets/72x72/";

        // Les images Fluent font 256 px : décodées à 72 px (assez net jusqu'à ~36 px à 200 % d'échelle Windows),
        // ce qui divise la mémoire par 12 — une grille de 140 emojis ne pèse presque rien.
        private const int DecodePixelWidth = 72;

        private static readonly Lazy<Dictionary<string, (string Folder, bool InDefault)>> FluentIndex = new(LoadFluentIndex);

        public static BitmapImage? Resolve(string? emoji)
        {
            if (string.IsNullOrEmpty(emoji)) return null;

            var codepoints = ToCodepoints(emoji);
            if (codepoints.Count == 0) return null;

            var key = string.Join("-", codepoints.Where(cp => cp != 0xFE0F).Select(cp => cp.ToString("x")));
            if (FluentIndex.Value.TryGetValue(key, out var entry))
                return RemoteImageCache.GetOrLoad(FluentUrl(entry.Folder, entry.InDefault), DecodePixelWidth);

            return RemoteImageCache.GetOrLoad($"{TwemojiBaseUrl}{ToTwemojiCodepoints(codepoints)}.png", DecodePixelWidth);
        }

        private static List<int> ToCodepoints(string emoji)
        {
            var codepoints = new List<int>();
            for (var i = 0; i < emoji.Length;)
            {
                codepoints.Add(char.ConvertToUtf32(emoji, i));
                i += char.IsSurrogatePair(emoji, i) ? 2 : 1;
            }
            return codepoints;
        }

        /// <summary>URL d'une image Fluent 3D : dossier = nom CLDR ; fichier = nom en minuscules, espaces → « _ ».
        /// Les emojis à teintes de peau ont leur version jaune par défaut dans « Default/ ».</summary>
        private static string FluentUrl(string folder, bool inDefault)
        {
            var stem = folder.ToLowerInvariant().Replace(' ', '_');
            var path = inDefault
                ? $"{Uri.EscapeDataString(folder)}/Default/3D/{stem}_3d_default.png"
                : $"{Uri.EscapeDataString(folder)}/3D/{stem}_3d.png";
            return FluentBaseUrl + path;
        }

        /// <summary>Lit l'index embarqué : une ligne « clé|dossier|1 si Default/ » par emoji (voir build_fluent_index.py).</summary>
        private static Dictionary<string, (string Folder, bool InDefault)> LoadFluentIndex()
        {
            var index = new Dictionary<string, (string, bool)>();
            try
            {
                using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("fluent-emoji-index.txt");
                if (stream is null) return index;
                using var reader = new StreamReader(stream);
                string? line;
                while ((line = reader.ReadLine()) is not null)
                {
                    if (line.Length == 0 || line[0] == '#') continue;
                    var parts = line.Split('|');
                    if (parts.Length == 3) index[parts[0]] = (parts[1], parts[2] == "1");
                }
            }
            catch
            {
                // Index illisible : tout retombe sur Twemoji plutôt que de planter.
            }
            return index;
        }

        /// <summary>
        /// Points de code Twemoji : hexadécimal minuscule joint par des tirets. Le sélecteur de variante U+FE0F est omis
        /// pour un emoji simple, mais conservé dans une séquence ZWJ (U+200D) — vérifié contre le dépôt Twemoji, les deux
        /// conventions y coexistent selon les fichiers.
        /// </summary>
        private static string ToTwemojiCodepoints(List<int> codepoints)
        {
            var isZwjSequence = codepoints.Contains(0x200D);
            var kept = isZwjSequence ? codepoints : codepoints.Where(cp => cp != 0xFE0F);
            return string.Join("-", kept.Select(cp => cp.ToString("x")));
        }
    }
}

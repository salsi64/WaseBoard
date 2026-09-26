using System.Collections.Generic;
using System.Linq;
using System.Windows.Media.Imaging;

namespace WaseBoard
{
    /// <summary>
    /// Résout un emoji Unicode vers son image couleur (Twemoji, via jsDelivr/jdecked/twemoji).
    /// Contourne l'incapacité de WPF à afficher le format d'emoji couleur récent de Windows 11
    /// (COLRv1) en glyphe texte, quelle que soit la police.
    /// </summary>
    public static class EmojiImageResolver
    {
        private const string BaseUrl = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@17.0.3/assets/72x72/";

        public static BitmapImage? Resolve(string? emoji)
        {
            if (string.IsNullOrEmpty(emoji)) return null;

            var codepoints = ToTwemojiCodepoints(emoji);
            if (codepoints.Length == 0) return null;

            return RemoteImageCache.GetOrLoad($"{BaseUrl}{codepoints}.png");
        }

        /// <summary>
        /// Points de code Unicode en hexadécimal, joints par des tirets. Le sélecteur de variante
        /// U+FE0F est omis pour un emoji simple, mais conservé dans une séquence ZWJ (U+200D) —
        /// vérifié contre le dépôt Twemoji, les deux conventions y coexistent selon les fichiers.
        /// </summary>
        private static string ToTwemojiCodepoints(string emoji)
        {
            var codepoints = new List<int>();
            for (var i = 0; i < emoji.Length;)
            {
                codepoints.Add(char.ConvertToUtf32(emoji, i));
                i += char.IsSurrogatePair(emoji, i) ? 2 : 1;
            }

            var isZwjSequence = codepoints.Contains(0x200D);
            var kept = isZwjSequence ? codepoints : codepoints.Where(cp => cp != 0xFE0F);
            return string.Join("-", kept.Select(cp => cp.ToString("x")));
        }
    }
}

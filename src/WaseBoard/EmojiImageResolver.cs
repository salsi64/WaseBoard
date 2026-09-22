using System.Collections.Generic;
using System.Linq;
using System.Windows.Media.Imaging;

namespace WaseBoard
{
    /// <summary>
    /// Résout un emoji Unicode vers son image couleur (Twemoji, servi depuis GitHub via jsDelivr —
    /// le package npm "twemoji" ne contient plus que le script d'analyse depuis la version 14, les
    /// images vivent dans le dépôt GitHub qui maintient Twemoji aujourd'hui, jdecked/twemoji).
    ///
    /// WPF ne sait pas afficher en couleur le format d'emoji le plus récent de Windows 11
    /// (COLRv1) — limitation connue et documentée du framework, pas un simple réglage de police :
    /// même avec la bonne FontFamily, le texte s'affiche en traits monochromes. On contourne ça en
    /// affichant de vraies images plutôt que le glyphe du son système.
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
        /// Convertit un emoji en nom de fichier Twemoji : points de code Unicode en hexadécimal
        /// minuscule, joints par des tirets — en gérant les paires de substitution (emoji hors du
        /// plan de base, ex: la plupart des emoji visage/objet).
        ///
        /// Le sélecteur de variante U+FE0F est omis pour un emoji simple (ex: "❤️" U+2764 U+FE0F
        /// → fichier "2764.png"), mais CONSERVÉ dès que la séquence contient un ZWJ (U+200D,
        /// emoji combinés — ex: "❤️‍🔥" U+2764 U+FE0F U+200D U+1F525 → fichier
        /// "2764-fe0f-200d-1f525.png", pas "2764-200d-1f525.png" qui n'existe pas dans Twemoji).
        /// Vérifié directement contre le contenu du dépôt Twemoji plutôt que supposé.
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

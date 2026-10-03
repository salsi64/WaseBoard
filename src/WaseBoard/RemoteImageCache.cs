using System;
using System.Collections.Concurrent;
using System.Windows.Media.Imaging;

namespace WaseBoard
{
    /// <summary>
    /// Charge et met en cache une image distante par URL (avatars/icônes Discord, emojis).
    /// `OnDemand` charge en arrière-plan sans bloquer l'UI (contrairement à `OnLoad`). Cache non
    /// borné, volontairement (peu d'images par session).
    /// </summary>
    internal static class RemoteImageCache
    {
        private static readonly ConcurrentDictionary<string, BitmapImage> _cache = new();

        /// <param name="decodePixelWidth">Largeur de décodage (0 = taille d'origine) : pour une petite icône tirée d'une
        /// grande image (emojis de 256 px affichés à ~20 px), évite de garder l'image entière en mémoire.</param>
        public static BitmapImage? GetOrLoad(string url, int decodePixelWidth = 0)
        {
            if (string.IsNullOrWhiteSpace(url)) return null;
            if (_cache.TryGetValue(url, out var cached)) return cached;

            try
            {
                var image = new BitmapImage();
                image.BeginInit();
                image.UriSource = new Uri(url, UriKind.Absolute);
                if (decodePixelWidth > 0) image.DecodePixelWidth = decodePixelWidth;
                image.CacheOption = BitmapCacheOption.OnDemand;
                image.EndInit();
                _cache[url] = image;
                return image;
            }
            catch
            {
                return null; // image non chargeable : on affiche simplement rien plutôt que de planter
            }
        }
    }
}

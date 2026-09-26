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

        public static BitmapImage? GetOrLoad(string url)
        {
            if (string.IsNullOrWhiteSpace(url)) return null;
            if (_cache.TryGetValue(url, out var cached)) return cached;

            try
            {
                var image = new BitmapImage();
                image.BeginInit();
                image.UriSource = new Uri(url, UriKind.Absolute);
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

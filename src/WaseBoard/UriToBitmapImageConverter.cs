using System;
using System.Collections.Concurrent;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media.Imaging;

namespace WaseBoard
{
    /// <summary>
    /// Convertit une URL (string) en image, pour afficher les avatars/icônes Discord téléchargés
    /// à la volée (avatars, icônes de serveur...).
    ///
    /// Deux problèmes réglés ici :
    /// - `BitmapCacheOption.OnLoad` force le décodage à se terminer DANS EndInit(), donc pour une
    ///   URL distante, ça bloque le thread UI jusqu'à la fin du téléchargement HTTP — c'est ce qui
    ///   donnait l'impression que les icônes prenaient "une éternité" (toute l'interface gelait
    ///   pendant le fetch). `OnDemand` laisse WPF télécharger/décoder en arrière-plan et
    ///   rafraîchir l'affichage une fois prêt, sans bloquer.
    /// - RefreshSections() reconstruit systématiquement tous les ViewModels (donc réévalue ce
    ///   convertisseur) à chaque action ou clic sur "Actualiser" : sans cache, chaque son/avatar/
    ///   icône déjà affiché était retéléchargé depuis zéro à chaque fois. Le cache statique par
    ///   URL élimine ces re-téléchargements (volontairement non borné : ce sont quelques dizaines
    ///   d'avatars/icônes par session, pas une source de fuite mémoire significative ici).
    /// </summary>
    public class UriToBitmapImageConverter : IValueConverter
    {
        private static readonly ConcurrentDictionary<string, BitmapImage> _cache = new();

        public object? Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is not string url || string.IsNullOrWhiteSpace(url)) return null;

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
                return null; // avatar non chargeable : on affiche simplement rien plutôt que de planter
            }
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

using System;
using System.Globalization;
using System.Windows.Data;

namespace WaseBoard
{
    /// <summary>Convertit une URL (string) en image, pour afficher les avatars/icônes Discord
    /// téléchargés à la volée (avatars, icônes de serveur...). Voir RemoteImageCache pour le
    /// chargement asynchrone et la mise en cache.</summary>
    public class UriToBitmapImageConverter : IValueConverter
    {
        public object? Convert(object? value, Type targetType, object parameter, CultureInfo culture) =>
            value is string url ? RemoteImageCache.GetOrLoad(url) : null;

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

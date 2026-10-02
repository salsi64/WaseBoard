using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace WaseBoard
{
    /// <summary>
    /// (TrimStartMs, TrimEndMs, DurationMs) → masque d'opacité horizontal pour la mini-waveform d'un son : la portion
    /// gardée est pleinement visible, le reste du son complet est estompé. Aucune découpe (ou durée encore inconnue)
    /// → masque opaque, donc waveform normale. Les positions sont relatives (0-1), indépendantes de la taille du dessin.
    /// </summary>
    public class TrimMaskConverter : IMultiValueConverter
    {
        public object Convert(object[] values, Type targetType, object parameter, CultureInfo culture)
        {
            if (values.Length < 3 || values[0] is not int start || values[1] is not int end
                || values[2] is not double duration || duration <= 0 || end <= start)
                return Brushes.Black;

            var from = Math.Clamp(start / duration, 0, 1);
            var to = Math.Clamp(end / duration, 0, 1);
            var dim = Color.FromArgb(56, 0, 0, 0);
            var full = Color.FromArgb(255, 0, 0, 0);

            // Arrêts de même position = bord net (pas de dégradé) entre la zone estompée et la zone gardée.
            var mask = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 0) };
            mask.GradientStops.Add(new GradientStop(dim, 0));
            mask.GradientStops.Add(new GradientStop(dim, from));
            mask.GradientStops.Add(new GradientStop(full, from));
            mask.GradientStops.Add(new GradientStop(full, to));
            mask.GradientStops.Add(new GradientStop(dim, to));
            mask.GradientStops.Add(new GradientStop(dim, 1));
            mask.Freeze();
            return mask;
        }

        public object[] ConvertBack(object value, Type[] targetTypes, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

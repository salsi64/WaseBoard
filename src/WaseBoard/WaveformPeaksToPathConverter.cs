using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace WaseBoard
{
    /// <summary>
    /// Convertit un tableau de crêtes (SoundItem.WaveformPeaks, 0-1) en Geometry affichable dans un
    /// Path (barres verticales, façon sparkline) — plus léger qu'un Rectangle par barre (TrimWindow)
    /// pour un grand catalogue de boutons. ConverterParameter = "largeur,hauteur" (ex: "120,14").
    /// </summary>
    public class WaveformPeaksToPathConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is not float[] peaks || peaks.Length == 0) return Geometry.Empty;

            double width = 120, height = 14;
            if (parameter is string dims)
            {
                var parts = dims.Split(',');
                if (parts.Length == 2 &&
                    double.TryParse(parts[0], NumberStyles.Any, CultureInfo.InvariantCulture, out var w) &&
                    double.TryParse(parts[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var h))
                {
                    width = w;
                    height = h;
                }
            }

            var geometry = new StreamGeometry();
            using (var ctx = geometry.Open())
            {
                var step = width / peaks.Length;
                for (var i = 0; i < peaks.Length; i++)
                {
                    var x = i * step + step / 2;
                    var barHeight = Math.Max(1.0, peaks[i] * height);
                    var yTop = (height - barHeight) / 2;
                    var yBottom = yTop + barHeight;
                    ctx.BeginFigure(new Point(x, yTop), false, false);
                    ctx.LineTo(new Point(x, yBottom), true, false);
                }
            }
            geometry.Freeze();
            return geometry;
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

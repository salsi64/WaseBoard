using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;
using WaseBoard.Models;

namespace WaseBoard
{
    /// <summary>Teinte très légère du fond d'un bouton de son : sa couleur choisie (Éditer le son > Couleur) ou, à défaut,
    /// sa couleur automatique (celle du halo de lecture). Une teinte translucide posée sur le fond plutôt qu'un fond
    /// mélangé : le bouton suit la palette en direct.</summary>
    public class SoundTintConverter : IValueConverter
    {
        private const byte TintAlpha = 0x26; // ≈ 15 %

        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is not SoundItem item) return Brushes.Transparent;
            try
            {
                var color = IdToColorConverter.ColorFor(item);
                var brush = new SolidColorBrush(Color.FromArgb(TintAlpha, color.R, color.G, color.B));
                brush.Freeze();
                return brush;
            }
            catch
            {
                return Brushes.Transparent;
            }
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

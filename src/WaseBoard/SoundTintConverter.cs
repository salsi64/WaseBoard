using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;
using WaseBoard.Models;

namespace WaseBoard
{
    /// <summary>Teinte très légère du fond d'un bouton de son, à partir de la couleur CHOISIE par l'utilisateur
    /// (Éditer le son > Couleur). Sans couleur choisie : transparent, le bouton garde le fond de la palette.
    /// Une teinte translucide posée sur le fond plutôt qu'un fond mélangé : le bouton suit la palette en direct.</summary>
    public class SoundTintConverter : IValueConverter
    {
        private const byte TintAlpha = 0x26; // ≈ 15 %

        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is not SoundItem { ColorHex: { Length: > 0 } hex }) return Brushes.Transparent;
            try
            {
                var color = (Color)ColorConverter.ConvertFromString(hex);
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

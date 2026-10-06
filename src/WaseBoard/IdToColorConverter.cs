using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace WaseBoard
{
    /// <summary>Dérive une couleur de badge à partir de l'ID d'un son (variété visuelle façon maquette, stable pour un même son).</summary>
    public class IdToColorConverter : IValueConverter
    {
        private static readonly string[] Palette =
        {
            "#F97316", "#8B5CF6", "#14B8A6", "#EC4899",
            "#3B82F6", "#EAB308", "#10B981", "#EF4444",
            "#6366F1", "#06B6D4"
        };

        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            // Le son entier est lié (et non son seul ID) : sa couleur choisie, si elle existe, passe avant la couleur automatique.
            if (value is Models.SoundItem item)
            {
                if (!string.IsNullOrEmpty(item.ColorHex))
                {
                    try { return new SolidColorBrush((Color)ColorConverter.ConvertFromString(item.ColorHex)); }
                    catch { /* couleur invalide enregistrée : retombe sur la couleur automatique */ }
                }
                value = item.Id;
            }
            if (value is not string s || s.Length == 0) return Brushes.Gray;

            var hash = 0;
            foreach (var c in s) hash = hash * 31 + c;

            var hex = Palette[Math.Abs(hash) % Palette.Length];
            return new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

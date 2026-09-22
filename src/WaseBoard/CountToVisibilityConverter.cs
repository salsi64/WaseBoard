using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace WaseBoard
{
    /// <summary>0 -> Collapsed, tout le reste -> Visible. Utilisé pour masquer la barre "now playing" quand rien ne joue.</summary>
    public class CountToVisibilityConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            var nonZero = value is int count && count > 0;
            var invert = "Invert".Equals(parameter as string, StringComparison.OrdinalIgnoreCase);
            return (invert ? !nonZero : nonZero) ? Visibility.Visible : Visibility.Collapsed;
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

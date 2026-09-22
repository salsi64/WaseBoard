using System;
using System.Globalization;
using System.Windows.Data;

namespace WaseBoard
{
    /// <summary>True si la chaîne n'est ni null ni vide/blanche. Utilisé pour n'afficher le badge de raccourci que si un raccourci est assigné.</summary>
    public class StringNotEmptyConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture) =>
            !string.IsNullOrWhiteSpace(value as string);

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

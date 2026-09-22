using System;
using System.Globalization;
using System.Windows.Data;

namespace WaseBoard
{
    /// <summary>Convertit un emoji (string) en image couleur via EmojiImageResolver. Le
    /// ConverterParameter sert de repli (ex: "🔊") quand la valeur liée est vide/nulle.</summary>
    public class EmojiToImageSourceConverter : IValueConverter
    {
        public object? Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            var emoji = value as string;
            if (string.IsNullOrEmpty(emoji)) emoji = parameter as string;
            return EmojiImageResolver.Resolve(emoji);
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            throw new NotSupportedException();
    }
}

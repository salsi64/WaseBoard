using System.Windows;
using System.Windows.Controls;

namespace WaseBoard
{
    /// <summary>Choisit le gabarit d'un bouton de son : classique (carte) ou moderne (pilule) selon ThemeState.IsModern.</summary>
    public class SoundButtonTemplateSelector : DataTemplateSelector
    {
        public override DataTemplate? SelectTemplate(object? item, DependencyObject container)
        {
            if (container is not FrameworkElement element) return null;

            var key = ThemeState.IsModern ? "ModernSoundButtonTemplate" : "SoundButtonTemplate";
            return element.TryFindResource(key) as DataTemplate;
        }
    }
}

using System;
using System.Windows.Markup;

namespace WaseBoard.Services.Localization
{
    /// <summary>
    /// Texte localisé dans le XAML : <c>xmlns:loc="clr-namespace:WaseBoard.Services.Localization"</c> puis
    /// <c>Content="{loc:Loc settings.title}"</c>. Évalué une fois, à la création de la fenêtre (la langue ne change
    /// qu'au redémarrage, voir <see cref="Loc"/>).
    /// </summary>
    [MarkupExtensionReturnType(typeof(string))]
    public class LocExtension : MarkupExtension
    {
        public LocExtension()
        {
        }

        public LocExtension(string key)
        {
            Key = key;
        }

        [ConstructorArgument("key")]
        public string Key { get; set; } = "";

        public override object ProvideValue(IServiceProvider serviceProvider) => Loc.T(Key);
    }
}

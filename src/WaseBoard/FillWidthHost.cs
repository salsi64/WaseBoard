using System.Windows;
using System.Windows.Controls;

namespace WaseBoard
{
    /// <summary>
    /// Conteneur qui REMPLIT la largeur qu'on lui donne sans jamais en réclamer : sa largeur désirée est nulle.
    /// Sert à la mini-waveform des pilules : une forme étirée (Stretch=Fill) réclamerait sinon toute la largeur
    /// offerte (jusqu'au MaxWidth de la pilule) et toutes les pilules feraient la même largeur ; ici c'est le nom
    /// du son qui fixe la largeur de la pilule, et la waveform s'y étire.
    /// </summary>
    public class FillWidthHost : Decorator
    {
        protected override Size MeasureOverride(Size constraint)
        {
            if (Child is null) return new Size(0, 0);
            Child.Measure(new Size(0, constraint.Height));
            return new Size(0, Child.DesiredSize.Height);
        }

        protected override Size ArrangeOverride(Size arrangeSize)
        {
            Child?.Arrange(new Rect(0, 0, arrangeSize.Width, arrangeSize.Height));
            return arrangeSize;
        }
    }
}

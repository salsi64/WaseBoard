using System;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace WaseBoard.Services
{
    /// <summary>Petites animations réutilisables, même esprit que la technique FLIP d'AnimatedWrapPanel
    /// (RenderTransform léger, CubicEase, 100-400ms).</summary>
    public static class AnimationHelpers
    {
        /// <summary>Rebond bref au clic (1.0 → 0.92 → 1.0), additif au glow IsPlaying existant.</summary>
        public static void PlayBounce(FrameworkElement target)
        {
            if (target.RenderTransform is not ScaleTransform scale || target.RenderTransform.IsFrozen)
            {
                scale = new ScaleTransform(1, 1);
                target.RenderTransform = scale;
                target.RenderTransformOrigin = new Point(0.5, 0.5);
            }

            var down = new DoubleAnimation(1.0, 0.92, TimeSpan.FromMilliseconds(70))
            {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
            };
            var up = new DoubleAnimation(0.92, 1.0, TimeSpan.FromMilliseconds(110))
            {
                BeginTime = TimeSpan.FromMilliseconds(70),
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
            };

            var storyboard = new Storyboard();
            foreach (var anim in new[] { down, up })
            {
                Storyboard.SetTarget(anim, target);
                Storyboard.SetTargetProperty(anim,
                    new PropertyPath("(0).(1)", UIElement.RenderTransformProperty, ScaleTransform.ScaleXProperty));
                storyboard.Children.Add(anim);
            }

            var downY = down.Clone();
            var upY = up.Clone();
            foreach (var anim in new[] { downY, upY })
            {
                Storyboard.SetTarget(anim, target);
                Storyboard.SetTargetProperty(anim,
                    new PropertyPath("(0).(1)", UIElement.RenderTransformProperty, ScaleTransform.ScaleYProperty));
                storyboard.Children.Add(anim);
            }

            storyboard.Begin();
        }
    }
}

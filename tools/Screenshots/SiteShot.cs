using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;
using WaseBoard.Models;
using WaseBoard.Services.Localization;

/// <summary>Captures « vitrine » du site : vraie interface (Flat), données d'exemple propres, titre « WaseBoard ».</summary>
static class SiteShot
{
    const BindingFlags NP = BindingFlags.NonPublic | BindingFlags.Instance;

    static string T(string fr, string en) => Loc.Language == "en" ? en : fr;

    static void Pump(int ms)
    {
        var end = DateTime.Now.AddMilliseconds(ms);
        while (DateTime.Now < end) { Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.Background); Thread.Sleep(20); }
    }

    static string Avatar(string letter, string hex)
    {
        var path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), $"winshot-avatar-{letter}.png");
        var color = (Color)ColorConverter.ConvertFromString(hex);
        var dv = new DrawingVisual();
        using (var dc = dv.RenderOpen())
        {
            dc.DrawEllipse(new SolidColorBrush(color), null, new Point(32, 32), 32, 32);
            var ft = new FormattedText(letter, System.Globalization.CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
                new Typeface("Segoe UI Semibold"), 30, Brushes.White, 1.0);
            dc.DrawText(ft, new Point(32 - ft.Width / 2, 32 - ft.Height / 2));
        }
        var rtb = new RenderTargetBitmap(64, 64, 96, 96, PixelFormats.Pbgra32);
        rtb.Render(dv);
        var enc = new PngBitmapEncoder(); enc.Frames.Add(BitmapFrame.Create(rtb));
        using (var fs = File.Create(path)) enc.Save(fs);
        return new Uri(path).AbsoluteUri;
    }

    static SoundItem Snd(string name, string emoji, string? color = null, bool playing = false, bool fav = false, params (string user, string url)[] players)
    {
        var rnd = new Random(name.GetHashCode());
        var peaks = new float[28];
        for (int i = 0; i < peaks.Length; i++) peaks[i] = 0.15f + (float)rnd.NextDouble() * 0.85f;
        var s = new SoundItem { Name = name, Emoji = emoji, ColorHex = color, IsPlaying = playing, IsFavorite = fav, WaveformPeaks = peaks, DurationMs = 3800, CanEdit = true };
        foreach (var (user, url) in players) s.ActiveUsers.Add(new UserActivity { UserId = user, Username = user, AvatarUrl = url });
        return s;
    }

    public static void Run(string outPng, string palette, int width, int height)
    {
        var app = new WaseBoard.App();
        app.InitializeComponent();
        typeof(Application).GetField("_startupUri", NP)?.SetValue(app, null);
        app.ShutdownMode = ShutdownMode.OnExplicitShutdown;

        var w = new WaseBoard.MainWindow();
        var t = typeof(WaseBoard.MainWindow);
        var libField = t.GetField("_library", NP)!;

        void ApplySettings()
        {
            var lib = libField.GetValue(w)!;
            var st = lib.GetType().GetProperty("Settings")!.GetValue(lib)!;
            void Set(string n, object v) => st.GetType().GetProperty(n)!.SetValue(st, v);
            Set("UiTheme", "Flat"); Set("PaletteId", palette); Set("FollowSystemTheme", false); Set("FollowSystemAccent", false); Set("ShowWaveforms", true);
            t.GetMethod("ApplyColorScheme", NP)!.Invoke(w, null);
            t.GetMethod("ApplyTheme", NP)!.Invoke(w, null);
        }
        ApplySettings();

        var a1 = Avatar("A", "#F472B6"); var a2 = Avatar("B", "#38BDF8"); var a3 = Avatar("C", "#34D399");

        var secType = t.GetNestedType("SectionViewModel", BindingFlags.NonPublic)!;
        object Section(string name, string key, bool manageable, bool shared, bool allSounds, params SoundItem[] sounds)
        {
            var s = Activator.CreateInstance(secType)!;
            secType.GetProperty("Name")!.SetValue(s, name);
            secType.GetProperty("CategoryKey")!.SetValue(s, key);
            secType.GetProperty("IsManageable")!.SetValue(s, manageable);
            secType.GetProperty("IsShared")!.SetValue(s, shared);
            secType.GetProperty("IsExpanded")!.SetValue(s, true);
            secType.GetProperty("IsReorderable")!.SetValue(s, manageable || shared);
            secType.GetProperty("IsAllSounds")!.SetValue(s, allSounds);
            secType.GetProperty("Sounds")!.SetValue(s, new ObservableCollection<SoundItem>(sounds));
            return s;
        }
        var list = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(secType))!;
        list.Add(Section(Loc.T("main.nav.favorites"), "__fav__", false, false, false,
            Snd(T("Applaudissements", "Applause"), "👏", "#38BDF8", false, true), Snd(T("Victoire", "Victory"), "🏆", "#F472B6", true, true, ("Alice", a1), ("Bob", a2)),
            Snd(T("Rire", "Laugh"), "😂", "#34D399", false, true), Snd(T("Alerte", "Alert"), "🚨", "#F87171", false, true)));
        list.Add(Section(T("Mèmes", "Memes"), "Mèmes", true, false, false,
            Snd("Bruh", "💀", "#A78BFA"), Snd("Vine boom", "💥", "#FBBF24"), Snd("Oh no", "😱"), Snd("Sad violin", "🎻", "#A78BFA"),
            Snd("Airhorn", "📯", "#22D3EE"), Snd("Wow", "🤩")));
        list.Add(Section(T("Effets", "Effects"), "Effets", true, false, false,
            Snd("Explosion", "💥", "#F87171"), Snd("Fanfare", "🎺", "#FBBF24"), Snd(T("Tambour", "Drum"), "🥁"), Snd(T("Cloche", "Bell"), "🔔", "#34D399")));
        list.Add(Section("Ballistic Potatoes", "1001", false, true, true,
            Snd(T("Bienvenue", "Welcome"), "👋"), Snd("Bravo", "🎉", "#F472B6"), Snd(T("Applaudissements très longs", "Very long applause"), "👏", "#38BDF8"), Snd(T("Mystère", "Mystery"), "🔮", "#A78BFA")));

        var items = (ItemsControl)w.FindName("SectionsItemsControl");
        var root = (FrameworkElement)w.Content;

        void Populate()
        {
            ((WaseBoard.Windows.Chrome.TitleBarControl)w.FindName("ChromeTitleBar")).TitleText = "WaseBoard";
            ((TextBlock)w.FindName("SidebarHeaderTitle")).Text = "🎛️ WaseBoard";
            items.ItemsSource = list;

            var navType = t.GetNestedType("NavItem", BindingFlags.NonPublic)!;
            object Nav(string label, int idx, string key)
            {
                var n = Activator.CreateInstance(navType)!;
                navType.GetProperty("Label")!.SetValue(n, label); navType.GetProperty("Index")!.SetValue(n, idx);
                navType.GetProperty("CategoryKey")!.SetValue(n, key);
                return n;
            }
            var servers = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(navType))!; servers.Add(Nav("Ballistic Potatoes", 3, "1001"));
            var cats = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(navType))!; cats.Add(Nav(T("Mèmes", "Memes"), 1, "Mèmes")); cats.Add(Nav(T("Effets", "Effects"), 2, "Effets"));
            ((ItemsControl)w.FindName("SidebarServersList")).ItemsSource = servers;
            ((ItemsControl)w.FindName("SidebarCategoriesList")).ItemsSource = cats;
            ((TextBlock)w.FindName("NavServersCount")).Text = "1"; ((TextBlock)w.FindName("NavCategoriesCount")).Text = "2";
            ((FrameworkElement)w.FindName("NavServersGroup")).Visibility = Visibility.Visible; ((FrameworkElement)w.FindName("NavCategoriesGroup")).Visibility = Visibility.Visible;
            ((ToggleButton)w.FindName("NavServersToggle")).IsChecked = true; ((ToggleButton)w.FindName("NavCategoriesToggle")).IsChecked = true;

            var now = (ObservableCollection<NowPlayingEntry>)t.GetProperty("NowPlayingEntries")!.GetValue(w)!;
            if (now.Count == 0)
            {
                now.Add(new NowPlayingEntry { SoundId = "1", UserId = "a", SoundName = T("Victoire", "Victory"), Emoji = "🏆", Username = "Alice", AvatarUrl = a1 });
                now.Add(new NowPlayingEntry { SoundId = "2", UserId = "b", SoundName = "Vine boom", Emoji = "💥", Username = "Bob", AvatarUrl = a2 });
            }
            var voice = (ObservableCollection<UserActivity>)t.GetProperty("VoiceChannelMembers")!.GetValue(w)!;
            if (voice.Count == 0)
            {
                voice.Add(new UserActivity { UserId = "a", Username = "Alice", AvatarUrl = a1, IsActive = true });
                voice.Add(new UserActivity { UserId = "b", Username = "Bob", AvatarUrl = a2 });
                voice.Add(new UserActivity { UserId = "c", Username = "Chloé", AvatarUrl = a3 });
            }
            ((Ellipse)w.FindName("StatusDot")).Fill = new SolidColorBrush(Color.FromRgb(0x4C, 0xAF, 0x50));
            ((TextBlock)w.FindName("ServerStatusText")).Text = Loc.T("main.server.connected", 24);
            ((TextBlock)w.FindName("SidebarConnectionText")).Text = Loc.Language == "en" ? "In voice: General" : "En vocal : Général";
            foreach (var n in new[] { "JoinVoiceLabel", "StopAllLabel", "AddCategoryLabel", "ClipButton", "ClipRecDot" }) ((FrameworkElement)w.FindName(n)).Visibility = Visibility.Visible;
            ((TextBlock)w.FindName("AddSoundLabel")).Text = Loc.T("main.add_sound");
        }

        void FixCombos(DependencyObject d)
        {
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(d); i++)
            {
                var c = VisualTreeHelper.GetChild(d, i);
                if (c is ComboBox cb && cb.SelectedIndex < 0 && cb.Items.Count > 0) cb.SelectedIndex = 0;
                FixCombos(c);
            }
        }
        void Layout() { root.Measure(new Size(width, height)); root.Arrange(new Rect(0, 0, width, height)); root.UpdateLayout(); }
        void Open(DependencyObject d)
        {
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(d); i++)
            {
                var c = VisualTreeHelper.GetChild(d, i);
                if (c is FrameworkElement fe && fe.Name == "ExpandSiteBorder" && fe.TemplatedParent is Expander ex && ex.IsExpanded) fe.MaxHeight = double.PositiveInfinity;
                Open(c);
            }
        }

        Populate(); Layout(); Pump(900);
        // Window_Loaded (déclenché par le Pump) recharge les réglages : on ré-applique thème + données.
        ApplySettings(); Populate(); Layout(); Pump(500);
        Open(root); Layout(); Pump(500); Populate(); Layout(); FixCombos(root); Layout(); Pump(300);

        var scale = double.Parse(Environment.GetEnvironmentVariable("SHOT_SCALE") ?? "2", System.Globalization.CultureInfo.InvariantCulture);
        var rtb = new RenderTargetBitmap((int)(width * scale), (int)(height * scale), 96 * scale, 96 * scale, PixelFormats.Pbgra32);
        rtb.Render(root);
        var enc = new PngBitmapEncoder(); enc.Frames.Add(BitmapFrame.Create(rtb));
        using (var fs = File.Create(outPng)) enc.Save(fs);
        Console.WriteLine($"SITE OK {outPng}");
        app.Shutdown();
    }
}

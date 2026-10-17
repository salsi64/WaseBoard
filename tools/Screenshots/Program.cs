using System;
using System.IO;

/// <summary>
/// Outil de développement : fabrique des captures d'écran de la vraie interface de WaseBoard (fenêtres de paramètres, d'édition,
/// fenêtre principale avec des données d'exemple…) pour le site, le README et la FAQ. Voir README.md de ce dossier.
/// </summary>
static class P
{
    [STAThread]
    static int Main(string[] args)
    {
        // win : une fenêtre ; main : la fenêtre principale (données d'exemple) ; site : capture « vitrine » (titre WaseBoard, avatars)
        if (args.Length > 3 && args[2] == "win") { MakeWav(Path.Combine(Path.GetTempPath(), "winshot.wav")); WinShot.Run(args[0], args[1], args[3]); return 0; }
        if (args.Length > 4 && args[2] == "main") { MainShot.Run(args[0], args[1], args[3], int.Parse(args[4]), 680); return 0; }
        if (args.Length > 3 && args[2] == "site") { SiteShot.Run(args[0], args[1], int.Parse(args[3]), args.Length > 4 ? int.Parse(args[4]) : 660); return 0; }

        Console.Error.WriteLine("""
            Usage (depuis la racine du dépôt) :
              dotnet run --project tools/Screenshots -c Dev -- <sortie.png> <Classic|Flat> win <fenêtre>
              dotnet run --project tools/Screenshots -c Dev -- <sortie.png> <Classic|Flat> main <palette> <largeur>
              dotnet run --project tools/Screenshots -c Dev -- <sortie.png> <palette> site <largeur> [hauteur]
            Fenêtres : settings, discord, micpage, clippage, diag, cliptrim, onboarding, confirm, prompt, alert, emoji, hotkey, edit, editlocal, siteedit, glyphs
            Palettes : NightBlue, Violet, Mint, Rose, Amber.   SHOT_SCALE=2 : facteur d'échelle ;   WASEBOARD_LANG=en : interface en anglais.
            """);
        return 2;
    }

    // Petit son d'essai (bruit à enveloppe) pour les fenêtres qui affichent une forme d'onde.
    internal static void MakeWav(string path, double seconds = 3.8, int seed = 7)
    {
        int rate = 44100, n = (int)(rate * seconds);
        var rnd = new Random(seed);
        using var fs = File.Create(path);
        using var w = new BinaryWriter(fs);
        w.Write(System.Text.Encoding.ASCII.GetBytes("RIFF")); w.Write(36 + n * 2);
        w.Write(System.Text.Encoding.ASCII.GetBytes("WAVEfmt ")); w.Write(16); w.Write((short)1); w.Write((short)1);
        w.Write(rate); w.Write(rate * 2); w.Write((short)2); w.Write((short)16);
        w.Write(System.Text.Encoding.ASCII.GetBytes("data")); w.Write(n * 2);
        for (int i = 0; i < n; i++)
        {
            double t = (double)i / rate;
            double env = (i % (rate / 2) < rate / 6 ? 1.0 : 0.25) * Math.Exp(-t * 0.45);
            w.Write((short)((rnd.NextDouble() * 2 - 1) * 30000 * env));
        }
    }
}

using System.Globalization;
using WaseBoard.Services.Localization;

namespace WaseBoard.Tests;

public class LocTests
{
    private static CultureInfo Culture(string name) => CultureInfo.GetCultureInfo(name);

    [Theory]
    [InlineData("auto", null, "fr-FR", "fr")]
    [InlineData("auto", null, "fr-CA", "fr")]
    [InlineData("auto", null, "en-US", "en")]
    [InlineData("auto", null, "de-DE", "en")]       // langue de Windows non gérée : anglais
    [InlineData(null, null, "fr-FR", "fr")]
    [InlineData("en", null, "fr-FR", "en")]         // la préférence l'emporte sur Windows
    [InlineData("fr", null, "en-US", "fr")]
    [InlineData("EN", null, "fr-FR", "en")]         // insensible à la casse
    [InlineData("xx", null, "en-US", "en")]         // préférence inconnue : comme « auto »
    [InlineData("en", "fr", "en-US", "fr")]         // la variable d'environnement passe avant tout
    [InlineData("auto", " en ", "fr-FR", "en")]
    public void ResolveLanguage(string? preference, string? environment, string windows, string expected)
    {
        Assert.Equal(expected, Loc.ResolveLanguage(preference, environment, Culture(windows)));
    }

    [Fact]
    public void Texts_come_from_the_selected_language()
    {
        Loc.Initialize("en");
        Assert.Contains("new version", Loc.T("update.available", "3.4.0"));
        Loc.Initialize("fr");
        Assert.Contains("nouvelle version", Loc.T("update.available", "3.4.0"));
    }

    [Fact]
    public void Parameters_are_substituted()
    {
        Loc.Initialize("fr");
        Assert.Contains("v3.4.0", Loc.T("update.available", "3.4.0"));
    }

    [Fact]
    public void Missing_key_falls_back_to_the_key_itself()
    {
        Loc.Initialize("en");
        Assert.Equal("cle.inexistante", Loc.T("cle.inexistante"));
        Assert.Equal("cle.inexistante", Loc.T("cle.inexistante", 1, 2));
    }

    [Fact]
    public void Bad_format_never_throws()
    {
        // Un texte avec un paramètre en plus de ceux fournis ne doit pas faire planter l'interface.
        Loc.Initialize("fr");
        var text = Loc.T("report.client", "3.4.0");   // attend 4 paramètres, un seul fourni
        Assert.False(string.IsNullOrEmpty(text));
    }

    [Fact]
    public void All_languages_have_the_same_keys_and_parameters()
    {
        var reference = Loc.Keys("fr").ToHashSet();
        foreach (var language in Loc.Supported)
        {
            Assert.Equal(reference, Loc.Keys(language).ToHashSet());
            Assert.NotEmpty(reference);
        }

        // Mêmes paramètres {n} dans chaque texte : un {2} oublié donnerait un message tronqué.
        foreach (var key in reference)
        {
            Loc.Initialize("fr");
            var french = ParameterNumbers(Loc.T(key));
            foreach (var language in Loc.Supported.Where(l => l != "fr"))
            {
                Loc.Initialize(language);
                Assert.True(french.SequenceEqual(ParameterNumbers(Loc.T(key))), $"[{language}] paramètres différents pour {key}");
            }
        }
    }

    [Fact]
    public void Every_key_used_in_the_sources_exists()
    {
        // Une faute de frappe dans Loc.T("...") ou {loc:Loc ...} afficherait la clé brute à l'utilisateur.
        var directory = new System.IO.DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && !System.IO.Directory.Exists(System.IO.Path.Combine(directory.FullName, "src", "WaseBoard", "Localization")))
            directory = directory.Parent;
        Assert.NotNull(directory);
        var sourceRoot = System.IO.Path.Combine(directory!.FullName, "src", "WaseBoard");

        var known = Loc.Keys("fr").ToHashSet();
        var separator = System.IO.Path.DirectorySeparatorChar;
        var missing = new List<string>();
        foreach (var file in System.IO.Directory.EnumerateFiles(sourceRoot, "*.*", System.IO.SearchOption.AllDirectories))
        {
            if (!(file.EndsWith(".cs") || file.EndsWith(".xaml"))) continue;
            if (file.Contains($"{separator}obj{separator}") || file.Contains($"{separator}bin{separator}")) continue;
            // Les exemples des commentaires (///) ne comptent pas.
            var text = string.Join(Environment.NewLine, System.IO.File.ReadAllLines(file).Where(line => !line.TrimStart().StartsWith("//")));
            var uses = System.Text.RegularExpressions.Regex.Matches(text, @"Loc\.T\(\s*""([A-Za-z0-9_.]+)""")
                .Concat(System.Text.RegularExpressions.Regex.Matches(text, @"\{loc:Loc\s+([A-Za-z0-9_.]+)\}"));
            foreach (System.Text.RegularExpressions.Match use in uses)
                if (!known.Contains(use.Groups[1].Value))
                    missing.Add($"{System.IO.Path.GetFileName(file)} : {use.Groups[1].Value}");
        }
        Assert.True(missing.Count == 0, "Clés de traduction inconnues : " + string.Join(", ", missing));
    }

    private static int[] ParameterNumbers(string text) =>
        System.Text.RegularExpressions.Regex.Matches(text, @"\{(\d+)")
            .Select(m => int.Parse(m.Groups[1].Value)).Distinct().OrderBy(n => n).ToArray();
}

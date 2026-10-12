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

    private static int[] ParameterNumbers(string text) =>
        System.Text.RegularExpressions.Regex.Matches(text, @"\{(\d+)")
            .Select(m => int.Parse(m.Groups[1].Value)).Distinct().OrderBy(n => n).ToArray();
}

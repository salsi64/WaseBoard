using System.Text.Json;
using WaseBoard.Services;

namespace WaseBoard.Tests;

public class PreferencesDocTests
{
    private static PreferencesDoc Make(string[]? favorites = null, Dictionary<string, string>? colors = null,
        Dictionary<string, List<string>>? categories = null, string[]? order = null) =>
        PreferencesDoc.Create(favorites ?? Array.Empty<string>(), colors ?? new(), categories ?? new(), order ?? Array.Empty<string>());

    [Fact]
    public void Les_doublons_sont_retires_les_noms_rognes_et_les_couleurs_mises_en_majuscules()
    {
        var doc = Make(new[] { "a", "b", "a" }, new() { ["a"] = "#aabbcc" },
            new() { ["Mèmes"] = new() { "x", "y", "x" }, ["  Jeux "] = new() { "z" } }, new[] { "Jeux", "42", "Jeux" });
        Assert.Equal(new[] { "a", "b" }, doc.Favorites);
        Assert.Equal("#AABBCC", doc.Colors["a"]);
        Assert.Equal(new[] { "x", "y" }, doc.Categories["Mèmes"]);
        Assert.Equal(new[] { "z" }, doc.Categories["Jeux"]);
        Assert.Equal(new[] { "Jeux", "42" }, doc.Order);
    }

    [Fact]
    public void Deux_noms_identiques_apres_rognage_sont_fusionnes()
    {
        var doc = Make(categories: new() { ["A"] = new() { "x" }, [" A "] = new() { "y", "x" } });
        Assert.Equal(new[] { "x", "y" }, doc.Categories["A"]);
    }

    [Fact]
    public void Ce_que_le_serveur_refuserait_est_laisse_de_cote_au_lieu_de_bloquer_toute_la_synchronisation()
    {
        var doc = Make(
            new[] { "", new string('x', PreferencesDoc.IdMax + 1), "ok" },
            new() { ["a"] = "rouge", ["b"] = "#abc", ["c"] = "#123456", [""] = "#111111" },
            new() { ["   "] = new() { "x" }, [new string('n', PreferencesDoc.CategoryNameMax + 1)] = new() { "x" }, ["Bon"] = new() { "", "id" } },
            new[] { "", "3101" });
        Assert.Equal(new[] { "ok" }, doc.Favorites);
        Assert.Equal(new[] { "c" }, doc.Colors.Keys.ToArray());
        Assert.Equal(new[] { "Bon" }, doc.Categories.Keys.ToArray());
        Assert.Equal(new[] { "id" }, doc.Categories["Bon"]);
        Assert.Equal(new[] { "3101" }, doc.Order);
    }

    [Fact]
    public void Les_limites_du_serveur_sont_respectees()
    {
        var doc = Make(Enumerable.Range(0, PreferencesDoc.FavoritesMax + 50).Select(i => $"s{i}").ToArray(),
            Enumerable.Range(0, PreferencesDoc.ColorsMax + 50).ToDictionary(i => $"s{i}", _ => "#000000"),
            Enumerable.Range(0, PreferencesDoc.CategoriesMax + 5).ToDictionary(i => $"c{i}", _ => Enumerable.Range(0, PreferencesDoc.CategorySoundsMax + 5).Select(j => $"s{j}").ToList()),
            Enumerable.Range(0, PreferencesDoc.OrderMax + 50).Select(i => $"k{i}").ToArray());
        Assert.Equal(PreferencesDoc.FavoritesMax, doc.Favorites.Count);
        Assert.Equal(PreferencesDoc.ColorsMax, doc.Colors.Count);
        Assert.Equal(PreferencesDoc.CategoriesMax, doc.Categories.Count);
        Assert.All(doc.Categories.Values, ids => Assert.Equal(PreferencesDoc.CategorySoundsMax, ids.Count));
        Assert.Equal(PreferencesDoc.OrderMax, doc.Order.Count);
    }

    [Fact]
    public void Favoris_et_contenu_des_categories_sont_des_ensembles_mais_l_ordre_d_affichage_est_une_suite()
    {
        var a = Make(new[] { "a", "b" }, categories: new() { ["X"] = new() { "1", "2" } }, order: new[] { "X", "3101" });
        var sameSets = Make(new[] { "b", "a" }, categories: new() { ["X"] = new() { "2", "1" } }, order: new[] { "X", "3101" });
        var otherOrder = Make(new[] { "a", "b" }, categories: new() { ["X"] = new() { "1", "2" } }, order: new[] { "3101", "X" });
        Assert.True(a.SemanticEquals(sameSets));
        Assert.False(a.SemanticEquals(otherOrder));
        Assert.False(a.SemanticEquals(Make(new[] { "a" }, categories: new() { ["X"] = new() { "1", "2" } }, order: new[] { "X", "3101" })));
        Assert.False(a.SemanticEquals(Make(new[] { "a", "b" }, categories: new() { ["X"] = new() { "1" } }, order: new[] { "X", "3101" })));
        Assert.False(a.SemanticEquals(Make(new[] { "a", "b" }, categories: new() { ["Y"] = new() { "1", "2" } }, order: new[] { "X", "3101" })));
        Assert.False(a.SemanticEquals(null));
    }

    [Fact]
    public void Les_couleurs_sont_comparees_sans_tenir_compte_de_la_casse_d_origine()
    {
        Assert.True(Make(colors: new() { ["a"] = "#aabbcc" }).SemanticEquals(Make(colors: new() { ["a"] = "#AABBCC" })));
        Assert.False(Make(colors: new() { ["a"] = "#aabbcc" }).SemanticEquals(Make(colors: new() { ["a"] = "#aabbcd" })));
        Assert.False(Make(colors: new() { ["a"] = "#aabbcc" }).SemanticEquals(Make()));
    }

    [Fact]
    public void Le_json_est_celui_que_le_serveur_attend_et_se_relit_a_l_identique()
    {
        var doc = Make(new[] { "a" }, new() { ["a"] = "#AABBCC" }, new() { ["Mèmes"] = new() { "x" } }, new[] { "Mèmes", "3101" });
        using var parsed = JsonDocument.Parse(doc.ToJson());
        var root = parsed.RootElement;
        Assert.Equal("a", root.GetProperty("favorites")[0].GetString());
        Assert.Equal("#AABBCC", root.GetProperty("colors").GetProperty("a").GetString());
        Assert.Equal("x", root.GetProperty("categories").GetProperty("Mèmes")[0].GetString());
        Assert.Equal("3101", root.GetProperty("order")[1].GetString());
        Assert.True(doc.SemanticEquals(PreferencesDoc.FromJson(doc.ToJson())));
    }

    [Fact]
    public void Un_json_absent_ou_illisible_donne_null_et_un_json_incomplet_un_document_vide_la_ou_il_manque_quelque_chose()
    {
        Assert.Null(PreferencesDoc.FromJson(null));
        Assert.Null(PreferencesDoc.FromJson(""));
        Assert.Null(PreferencesDoc.FromJson("pas du json"));
        Assert.Null(PreferencesDoc.FromJson("[1, 2]"));
        var partial = PreferencesDoc.FromJson("{\"favorites\": [\"a\"]}");
        Assert.NotNull(partial);
        Assert.Equal(new[] { "a" }, partial!.Favorites);
        Assert.Empty(partial.Colors);
        Assert.Empty(partial.Categories);
    }

    [Fact]
    public void Un_nom_de_categorie_qui_s_appelle_proto_reste_un_simple_nom()
    {
        var doc = PreferencesDoc.FromJson("{\"categories\": {\"__proto__\": [\"a\"]}}");
        Assert.NotNull(doc);
        Assert.Equal(new[] { "a" }, doc!.Categories["__proto__"]);
    }
}

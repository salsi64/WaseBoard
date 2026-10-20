using System.Diagnostics;
using WaseBoard.Services;

namespace WaseBoard.Tests;

public class PollBackoffTests
{
    private static readonly TimeSpan Normal = TimeSpan.FromMilliseconds(300);
    private static readonly TimeSpan[] Steps = { TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(30) };

    private static PollBackoff Make() => new(Normal, Steps);

    [Fact]
    public void Starts_at_the_normal_rhythm()
    {
        var backoff = Make();
        Assert.Equal(Normal, backoff.Interval);
        Assert.Equal(0, backoff.ConsecutiveFailures);
        Assert.False(backoff.IsSlowed);
    }

    [Fact]
    public void One_or_two_failures_do_not_slow_anything_down()
    {
        var backoff = Make();
        Assert.False(backoff.RecordFailure());
        Assert.False(backoff.RecordFailure());
        Assert.Equal(Normal, backoff.Interval);
        Assert.Equal(2, backoff.ConsecutiveFailures);
    }

    [Fact]
    public void Slows_down_step_by_step_after_three_failures_and_stays_at_the_last_step()
    {
        var backoff = Make();
        backoff.RecordFailure();
        backoff.RecordFailure();

        Assert.True(backoff.RecordFailure());   // 3e échec : 2 s
        Assert.Equal(TimeSpan.FromSeconds(2), backoff.Interval);
        Assert.True(backoff.RecordFailure());   // 4e : 10 s
        Assert.Equal(TimeSpan.FromSeconds(10), backoff.Interval);
        Assert.True(backoff.RecordFailure());   // 5e : 30 s
        Assert.Equal(TimeSpan.FromSeconds(30), backoff.Interval);
        Assert.True(backoff.IsSlowed);

        for (var i = 0; i < 1000; i++) Assert.False(backoff.RecordFailure());   // plafond : plus de changement
        Assert.Equal(TimeSpan.FromSeconds(30), backoff.Interval);
    }

    [Fact]
    public void First_success_after_a_slowdown_restores_the_normal_rhythm_at_once()
    {
        var backoff = Make();
        for (var i = 0; i < 6; i++) backoff.RecordFailure();
        Assert.True(backoff.IsSlowed);

        Assert.True(backoff.RecordSuccess());
        Assert.Equal(Normal, backoff.Interval);
        Assert.Equal(0, backoff.ConsecutiveFailures);
        Assert.False(backoff.IsSlowed);
    }

    [Fact]
    public void A_success_between_failures_restarts_the_count()
    {
        var backoff = Make();
        backoff.RecordFailure();
        backoff.RecordFailure();
        Assert.False(backoff.RecordSuccess());   // jamais ralenti : rien à rétablir
        backoff.RecordFailure();
        backoff.RecordFailure();
        Assert.Equal(Normal, backoff.Interval);  // 2 échecs seulement depuis le succès
        Assert.True(backoff.RecordFailure());
        Assert.Equal(TimeSpan.FromSeconds(2), backoff.Interval);
    }

    [Fact]
    public void Success_with_nothing_wrong_changes_nothing()
    {
        var backoff = Make();
        for (var i = 0; i < 100; i++)
        {
            Assert.False(backoff.RecordSuccess());
            Assert.Equal(Normal, backoff.Interval);
            Assert.Equal(0, backoff.ConsecutiveFailures);
        }
    }

    [Fact]
    public void Reset_returns_to_the_normal_rhythm_and_reports_the_change_only_once()
    {
        var backoff = Make();
        for (var i = 0; i < 4; i++) backoff.RecordFailure();
        Assert.True(backoff.Reset());
        Assert.Equal(Normal, backoff.Interval);
        Assert.Equal(0, backoff.ConsecutiveFailures);
        Assert.False(backoff.Reset());
    }

    [Fact]
    public void Invalid_settings_are_refused()
    {
        Assert.Throws<ArgumentException>(() => new PollBackoff(Normal, Array.Empty<TimeSpan>()));
        Assert.Throws<ArgumentOutOfRangeException>(() => new PollBackoff(Normal, Steps, 0));
    }

    [Fact]
    public void A_server_that_always_refuses_is_polled_a_few_hundred_times_less_than_before()
    {
        // 4 h 30 de refus, comme le 18/10 : au rythme fixe de 300 ms, 54 000 requêtes ; avec le ralentissement, quelques centaines.
        var backoff = Make();
        var elapsed = TimeSpan.Zero;
        var requests = 0;
        var window = TimeSpan.FromHours(4.5);
        while (elapsed < window)
        {
            requests++;
            backoff.RecordFailure();
            elapsed += backoff.Interval;
        }
        Assert.InRange(requests, 1, 700);
        Assert.True(54_000 / requests > 70, $"seulement {requests} requêtes");
    }

    [Fact]
    public void Success_path_costs_next_to_nothing()
    {
        // Le chemin normal (un succès, aucun échec en cours) est exécuté toutes les 300 ms sur le fil de l'interface : il ne doit coûter
        // qu'une comparaison. Un million d'appels en bien moins d'une seconde, même sur une machine d'intégration continue chargée
        // (en pratique : quelques millisecondes ; en 4 h 30 à 300 ms, l'application en fait 54 000).
        var backoff = Make();
        var timer = Stopwatch.StartNew();
        var changed = 0;
        for (var i = 0; i < 1_000_000; i++) if (backoff.RecordSuccess()) changed++;
        timer.Stop();

        Assert.Equal(0, changed);
        Assert.True(timer.ElapsedMilliseconds < 3000, $"{timer.ElapsedMilliseconds} ms pour 1 000 000 de succès");
    }
}

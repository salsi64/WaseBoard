using WaseBoard.Services;

namespace WaseBoard.Tests;

public class ClipRingBufferTests
{
    // Mono 1 000 Hz : 1 s = 2 000 octets, donc 1 ms = 2 octets — des valeurs faciles à vérifier.
    private const int Rate = 1000;

    private sealed class Clock
    {
        public double Ms;
        public double Now() => Ms;
    }

    private static byte[] Pcm(int bytes, byte value) => Enumerable.Repeat(value, bytes).ToArray();

    /// <summary>Un paquet qui arrive « à l'heure » : l'horloge avance de sa durée, puis il est ajouté.</summary>
    private static void Feed(ClipRingBuffer buffer, Clock clock, byte[] data)
    {
        clock.Ms += data.Length * 1000.0 / buffer.BytesPerSecond;
        buffer.Append(data);
    }

    private static (ClipRingBuffer Buffer, Clock Clock) Make(double seconds = 1.0, int channels = 1)
    {
        var clock = new Clock();
        return (new ClipRingBuffer(Rate, channels, seconds, clock.Now), clock);
    }

    [Fact]
    public void Snapshot_returns_what_was_appended_in_order()
    {
        var (buffer, clock) = Make(1.0);
        Feed(buffer, clock, Pcm(200, 1));
        Feed(buffer, clock, Pcm(200, 2));

        var snapshot = buffer.Snapshot(1.0);

        Assert.Equal(400, snapshot.Length);
        Assert.All(snapshot.Take(200), b => Assert.Equal(1, b));
        Assert.All(snapshot.Skip(200), b => Assert.Equal(2, b));
    }

    [Fact]
    public void Oldest_audio_is_overwritten_once_the_buffer_is_full()
    {
        var (buffer, clock) = Make(1.0); // 2 000 octets
        for (byte i = 1; i <= 15; i++) Feed(buffer, clock, Pcm(200, i)); // 3 000 octets au total, chaque paquet 100 ms

        var snapshot = buffer.Snapshot(1.0);

        Assert.Equal(2000, snapshot.Length);
        Assert.Equal(6, snapshot[0]);      // les paquets 1 à 5 sont partis
        Assert.Equal(15, snapshot[^1]);
        Assert.Equal(2000, buffer.BufferedBytes);
    }

    [Fact]
    public void Snapshot_can_ask_for_less_than_the_capacity()
    {
        var (buffer, clock) = Make(1.0);
        Feed(buffer, clock, Pcm(600, 1));
        Feed(buffer, clock, Pcm(200, 2));

        var snapshot = buffer.Snapshot(0.1); // 100 ms = 200 octets : seulement le dernier paquet

        Assert.Equal(200, snapshot.Length);
        Assert.All(snapshot, b => Assert.Equal(2, b));
    }

    [Fact]
    public void A_packet_larger_than_the_buffer_keeps_only_its_end()
    {
        var (buffer, clock) = Make(1.0);
        var data = new byte[5000];
        for (var i = 0; i < data.Length; i++) data[i] = (byte)(i / 1000 + 1); // 1,1,…,2,…,5
        Feed(buffer, clock, data);

        var snapshot = buffer.Snapshot(1.0);

        Assert.Equal(2000, snapshot.Length);
        Assert.Equal(4, snapshot[0]);
        Assert.Equal(5, snapshot[^1]);
    }

    [Fact]
    public void A_silent_stretch_between_packets_is_filled_with_zeros()
    {
        // Windows ne livre aucun paquet pendant un silence : sans remplissage, le clip « raccourcirait » en temps réel.
        var (buffer, clock) = Make(10.0);
        Feed(buffer, clock, Pcm(400, 7));        // 200 ms de son
        clock.Ms += 3000;                        // 3 s de silence (rien reçu)
        Feed(buffer, clock, Pcm(400, 9));        // 200 ms de son

        var snapshot = buffer.Snapshot(10.0);

        Assert.Equal((200 + 3000 + 200) * 2, snapshot.Length);
        Assert.All(snapshot.Take(400), b => Assert.Equal(7, b));
        Assert.All(snapshot.Skip(400).Take(6000), b => Assert.Equal(0, b));
        Assert.All(snapshot.Skip(6400), b => Assert.Equal(9, b));
    }

    [Fact]
    public void Silence_after_the_last_packet_is_included_up_to_now()
    {
        // Le clip finit à l'instant de la demande, même si rien ne joue depuis 3 s : 1 s de son, puis 3 s de silence.
        var (buffer, clock) = Make(10.0);
        Feed(buffer, clock, Pcm(2000, 5));       // 1 s de son
        clock.Ms += 3000;

        var snapshot = buffer.Snapshot(5.0);

        Assert.Equal(4 * Rate * 2, snapshot.Length);
        Assert.All(snapshot.Take(2000), b => Assert.Equal(5, b));
        Assert.All(snapshot.Skip(2000), b => Assert.Equal(0, b));
    }

    [Fact]
    public void A_buffer_that_never_received_anything_is_all_silence_for_the_time_elapsed()
    {
        var (buffer, clock) = Make(10.0);
        clock.Ms += 4000;

        var snapshot = buffer.Snapshot(10.0);

        Assert.Equal(4 * Rate * 2, snapshot.Length);
        Assert.All(snapshot, b => Assert.Equal(0, b));
    }

    [Fact]
    public void Small_delays_are_not_mistaken_for_silence()
    {
        // Les paquets arrivent par rafales (fil occupé) : un retard de 100 ms n'est pas une pause.
        var (buffer, clock) = Make(10.0);
        Feed(buffer, clock, Pcm(200, 1));
        clock.Ms += 100;
        Feed(buffer, clock, Pcm(200, 2));

        Assert.Equal(400, buffer.Snapshot(10.0).Length);
    }

    [Fact]
    public void A_silence_longer_than_the_buffer_replaces_everything()
    {
        var (buffer, clock) = Make(1.0);
        Feed(buffer, clock, Pcm(2000, 3));
        clock.Ms += 60_000;

        var snapshot = buffer.Snapshot(1.0);

        Assert.Equal(2000, snapshot.Length);
        Assert.All(snapshot, b => Assert.Equal(0, b));
    }

    [Fact]
    public void An_incomplete_sample_at_the_end_of_a_packet_is_ignored()
    {
        var (buffer, clock) = Make(1.0, channels: 2); // 4 octets par trame
        clock.Ms += 10;
        buffer.Append(Pcm(10, 1)); // 2 trames entières + 2 octets en trop

        Assert.Equal(8, buffer.BufferedBytes);
        Assert.Equal(0, buffer.Snapshot(1.0).Length % 4);
    }

    [Fact]
    public void Snapshot_is_always_a_whole_number_of_frames()
    {
        var (buffer, clock) = Make(1.0, channels: 2);
        Feed(buffer, clock, Pcm(4000, 1));

        Assert.Equal(0, buffer.Snapshot(0.0333).Length % 4);
        Assert.Equal(0, buffer.Snapshot(1.0).Length % 4);
    }

    [Fact]
    public void Clear_empties_the_buffer_and_restarts_the_silence_count()
    {
        var (buffer, clock) = Make(10.0);
        Feed(buffer, clock, Pcm(2000, 5));
        clock.Ms += 2000;
        buffer.Clear();

        Assert.Equal(0, buffer.BufferedBytes);
        Assert.Empty(buffer.Snapshot(10.0));
    }

    [Fact]
    public void Asking_for_zero_or_negative_seconds_gives_an_empty_clip()
    {
        var (buffer, clock) = Make(1.0);
        Feed(buffer, clock, Pcm(400, 1));

        Assert.Empty(buffer.Snapshot(0));
        Assert.Empty(buffer.Snapshot(-3));
    }

    [Theory]
    [InlineData(0, 1, 1.0)]
    [InlineData(1000, 0, 1.0)]
    [InlineData(1000, 3, 1.0)]
    [InlineData(1000, 1, 0.0)]
    public void Invalid_construction_arguments_are_rejected(int rate, int channels, double seconds)
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new ClipRingBuffer(rate, channels, seconds));
    }

    [Fact]
    public void Capacity_matches_the_requested_duration()
    {
        var buffer = new ClipRingBuffer(48000, 2, 15);

        Assert.Equal(15.0, buffer.CapacitySeconds, 3);
        Assert.Equal(48000 * 2 * 2, buffer.BytesPerSecond);
    }

    [Fact]
    public async Task Concurrent_appends_and_snapshots_do_not_corrupt_or_throw()
    {
        // Le fil de capture ajoute pendant que l'interface demande un clip.
        var buffer = new ClipRingBuffer(48000, 2, 1);
        var failure = (Exception?)null;
        using var stop = new CancellationTokenSource();

        var writer = Task.Run(() =>
        {
            try
            {
                var packet = Pcm(1920, 1);
                while (!stop.IsCancellationRequested) buffer.Append(packet);
            }
            catch (Exception ex) { failure = ex; }
        });

        for (var i = 0; i < 300; i++)
        {
            var snapshot = buffer.Snapshot(1.0);
            Assert.Equal(0, snapshot.Length % 4);
            Assert.All(snapshot, b => Assert.True(b <= 1));
        }

        stop.Cancel();
        await writer;
        Assert.Null(failure);
    }
}

namespace Automathon.Game
{
    /// <summary>
    /// Rules of a match. The default values are those of the standard game (visual game, tournaments).
    /// The training Headless can override them at reset (AIMessage.ResetOptions.Rules) to test variants:
    /// fields left out of the JSON keep their default value.
    /// </summary>
    public class GameRules
    {
        /// <summary>Rules of the current match.</summary>
        public static GameRules Current { get; private set; } = new GameRules();

        /// <summary>Rules used from the next reset on (null = default rules).</summary>
        public static void Set(GameRules rules) => Current = rules ?? new GameRules();

        /// <summary>Health of a ship at the start of a match.</summary>
        public Fixed ShipMaxHealth = 1000;

        /// <summary>Damage of a machine gun bullet.</summary>
        public Fixed BulletDamage = 60;

        /// <summary>Damage of a missile to the ship it hits directly.</summary>
        public Fixed MissileDirectDamage = 600;

        /// <summary>Damage of a missile explosion to the ships in its area.</summary>
        public Fixed MissileAreaDamage = 400;

        /// <summary>Missiles turn towards the current aiming direction of their shooter (false = straight line).</summary>
        public bool MissileGuided = true;

        /// <summary>Lifespan of a shield, in seconds.</summary>
        public Fixed ShieldLifespan = 10;

        /// <summary>Maximum number of shields a ship can have at the same time; the oldest one disappears
        /// when a new one is deployed (0 = no limit).</summary>
        public int MaxActiveShields = 0;

        /// <summary>Duration of a match in seconds; when it is over, the ship with the most health wins,
        /// and equal health is a draw (0 = no time limit).</summary>
        public Fixed MatchDuration = 0;

        /// <summary>Distance a missile must travel before it is armed: a missile that collides with anything earlier
        /// fizzles out, without any damage (0 = armed at once).</summary>
        public Fixed MissileArmingDistance = 1;

        /// <summary>Health lost per second by a ship whose centre is outside the safe zone (0 = no zone). The safe zone
        /// is a rectangle centred on the arena: the whole arena until ZoneStart seconds, then it shrinks linearly for
        /// ZoneShrinkDuration seconds down to ZoneFinalScale times the arena, and stays there. With the default rules
        /// it closes completely at 40 s: from then on both ships lose health, and the one with the most health wins.</summary>
        public Fixed ZoneDamagePerSecond = 50;

        /// <summary>Time (seconds) at which the safe zone starts shrinking.</summary>
        public Fixed ZoneStart = 10;

        /// <summary>Time (seconds) the safe zone takes to shrink to its final size.</summary>
        public Fixed ZoneShrinkDuration = 30;

        /// <summary>Final size of the safe zone, as a fraction of the arena (same fraction on both axes; 0 = the zone
        /// closes completely).</summary>
        public Fixed ZoneFinalScale = 0;
    }
}

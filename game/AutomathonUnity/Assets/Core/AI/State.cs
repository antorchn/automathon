using System.Collections.Generic;
using static Automathon.Game.Tank;

namespace Automathon.Game
{
    public class GameState
    {
        public bool Done;
        public ShipState SelfShip;
        public ShipState EnemyShip;
        public List<Bullet.BulletState> BulletStates = new();
        public List<Missile.MissileState> MissileStates = new();
        public List<Wall.WallState> WallStates = new();
        public List<Shield.ShieldState> ShieldStates = new();

        /// <summary>"Ongoing", "Win", "Loss" or "Draw" (both ships destroyed), from your point of view.</summary>
        public string Outcome = "Ongoing";

        /// <summary>Frames simulated since the last reset (training Headless only, 0 otherwise).</summary>
        public int Frame;

        /// <summary>Damage dealt to the enemy ship during the last step, all frames included
        /// (training Headless only, 0 otherwise).</summary>
        public Fixed DamageDealt;

        /// <summary>Damage taken by your ship during the last step, all frames included
        /// (training Headless only, 0 otherwise).</summary>
        public Fixed DamageTaken;

        /// <summary>Half size of the safe zone (GameRules.ZoneDamagePerSecond), centred on the arena: the whole arena
        /// when there is no zone or before it starts shrinking.</summary>
        public FixedVector2 ZoneHalfSize;
    }

    public class State
    {
        public string Type => this.GetType().Name;
        /// <summary>World position (1 = 1 Unity unit).</summary>
        public FixedVector2 Position;
    }
}

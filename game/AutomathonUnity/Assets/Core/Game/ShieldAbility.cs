using Automathon.Engine;
using System;
using System.Collections.Generic;

namespace Automathon.Game
{
    public class ShieldAbility : Ability
    {
        private static readonly Fixed COOLDOWN = 2; // seconds
        private static readonly Fixed SPAWN_DISTANCE_FROM_TANK = 2;

        /// <summary>Shields of this tank still in the arena, oldest first.</summary>
        private readonly List<Shield> activeShields = new();

        public ShieldAbility(Func<bool> shouldActivate) : base(COOLDOWN, shouldActivate: shouldActivate)
        { }

        protected override void Activate()
        {
            // GameRules.MaxActiveShields: the oldest shields disappear to make room for the new one
            int maxShields = GameRules.Current.MaxActiveShields;
            while (maxShields > 0 && activeShields.Count >= maxShields)
            {
                Shield oldest = activeShields[0];
                activeShields.RemoveAt(0);
                GameplayManager.Destroy(oldest);
            }

            FixedVector2 direction = Tank.LastDirection.Normalized();

            FixedVector2 position = Tank.Position + direction * SPAWN_DISTANCE_FROM_TANK;
            Fixed rotation = direction.Angle() + Fixed.HalfPi;

            Shield shield = GameplayManager.Instantiate(new Shield(position, rotation, Tank));
            activeShields.Add(shield);
            shield.Destroyed += () => activeShields.Remove(shield);
        }
    }
}

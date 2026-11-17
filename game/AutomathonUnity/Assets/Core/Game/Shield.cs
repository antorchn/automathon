using Automathon.Engine;
using Automathon.Engine.Physics;

namespace Automathon.Game
{
    public class Shield : Entity
    {
        public class ShieldState : Wall.WallState
        {
            /// <summary>Team of the tank that deployed it ("Green" or "Red").</summary>
            public string Owner;
            public Fixed Health;
            public FixedVector2 Velocity;
        }

        public Rigidbody Rigidbody { get; private set; }
        private Health health;
        private static readonly Fixed MAX_HEALTH = 1500;
        private static readonly Fixed LENGTH = 1.5;
        private static readonly Fixed HEIGHT = 0.2;

        public Fixed Health => health.CurrentHealth;
        public BoxCollider BoxCollider { get; private set; }
        /// <summary>Tank that deployed the shield (null if unknown).</summary>
        public Tank Owner { get; }

        public Shield(FixedVector2 position, Fixed rotation, Tank owner = null) : base(position)
        {
            Owner = owner;
            Rotation = rotation;
            BoxCollider = new BoxCollider(FixedVector2.Zero, LENGTH, HEIGHT, 0);
            BoxCollider.Layer = CollisionLayer.Shield;
            // Very heavy: barely moves when hit
            Rigidbody = new Rigidbody(BoxCollider, invMass: 0.001, invInertia: 0.001, friction: 0);

            Initialize(Rigidbody, BoxCollider, health = new Health(MAX_HEALTH, true));

            AddBehavior(new Timer(GameRules.Current.ShieldLifespan, null, () => GameplayManager.Destroy(this)));
        }

        public override State GetState()
            => new ShieldState() { Owner = Owner?.Team.ToString(), Position = this.Position, Rotation = this.Rotation, Size = new FixedVector2(LENGTH, HEIGHT), Velocity = Rigidbody.Velocity, Health = health.CurrentHealth };
    }
}

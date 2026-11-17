using Automathon.Engine;
using Automathon.Engine.Physics;
using System;

namespace Automathon.Game
{
    public class Bullet : Entity
    {
        public class BulletState : State
        {
            /// <summary>Team of the tank that fired it ("Green" or "Red"), null for grenade fragments.</summary>
            public string Owner;
            public Fixed Radius;
            public FixedVector2 Velocity;
        }

        public static readonly Fixed RADIUS = 0.1;
        /// <summary>Speed in units per second.</summary>
        public static readonly Fixed SPEED = 17;
        private static readonly Fixed LIFESPAN = 10; // seconds
        /// <summary>
        /// Bullets go through the arena borders (LayerMatrix) and used to stay in the state for their whole lifespan.
        /// They are destroyed once they are 2 units beyond the borders' inner face (13.70 / 7.70): from there they can no
        /// longer reach a tank or a shield that is even partly inside the arena.
        /// </summary>
        private static readonly Fixed OUT_OF_ARENA_X = 15.7, OUT_OF_ARENA_Y = 9.7;

        private Tank shotFromTank;
        private bool destroyRequested;

        private Rigidbody rigidbody;
        private CircleCollider circleCollider;

        public Action HitWall;
        public Action HitTank;

        public Bullet(FixedVector2 position, FixedVector2 direction, Tank shotFrom) : base(position)
        {
            this.shotFromTank = shotFrom;

            direction = direction.Normalized();

            circleCollider = new CircleCollider(FixedVector2.Zero, RADIUS);
            circleCollider.Layer = CollisionLayer.Bullet;
            rigidbody = new Rigidbody(circleCollider, invMass: 10, invInertia: 0.3, friction: 0);

            Initialize(circleCollider, rigidbody);

            rigidbody.Velocity = direction * SPEED;
            circleCollider.OnCollision += OnCollision;

            AddBehavior(new Timer(LIFESPAN, null, RequestDestroy));
        }

        public override void Update()
        {
            base.Update();
            if (Position.X.Abs() > OUT_OF_ARENA_X || Position.Y.Abs() > OUT_OF_ARENA_Y)
                RequestDestroy();
        }

        /// <summary>Destroys the bullet once, whatever asks for it first in the frame (lifespan, hit, out of arena).</summary>
        private void RequestDestroy()
        {
            if (destroyRequested)
                return;
            destroyRequested = true;
            GameplayManager.Destroy(this);
        }

        private void OnCollision(CollisionEvent collisionContact)
        {
            if (collisionContact.Other.ParentEntity == shotFromTank)
                return;

            if (collisionContact.Other.ParentEntity is Wall wall)
            {
                wall.OnHit?.Invoke(collisionContact.Contacts[0].Position, 500);
                HitWall?.Invoke();
            }


            if (collisionContact.Other.Layer == CollisionLayer.Tank)
                HitTank?.Invoke();

            if (collisionContact.Other.ParentEntity.TryGetComponent(out Health health))
                health.Damage(GameRules.Current.BulletDamage);

            RequestDestroy();
        }

        public override void OnDestroyed()
        {
            //technically doing this isn't necessary since it's not a memory leak since circleCollider gets destroyed along with the entity
            //better safe than sound ig
            circleCollider.OnCollision -= OnCollision;
            base.OnDestroyed();
        }

        public override State GetState()
        {
            return new BulletState()
            {
                Owner = shotFromTank?.Team.ToString(),
                Position = this.Position,
                Radius = RADIUS,
                Velocity = rigidbody.Velocity,
            };
        }
    }
}

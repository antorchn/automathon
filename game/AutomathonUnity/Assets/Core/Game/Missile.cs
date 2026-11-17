using Automathon.Engine;
using Automathon.Engine.Physics;
using System.Collections.Generic;
using System.Linq;

namespace Automathon.Game
{
    public class Missile : Entity
    {
        public class MissileState : State
        {
            /// <summary>Team of the tank that fired it ("Green" or "Red").</summary>
            public string Owner;
            public Fixed Radius;
            public Fixed Rotation;
            public FixedVector2 Velocity;
            /// <summary>False while the missile has not travelled GameRules.MissileArmingDistance: a hit then does
            /// nothing.</summary>
            public bool Armed = true;
        }

        // Guidance (GameRules.MissileGuided) and damage (GameRules.MissileDirectDamage / MissileAreaDamage)
        // are rules of the match.

        public static readonly Fixed RADIUS = 0.3;
        /// <summary>Speed in units per second.</summary>
        public static readonly Fixed SPEED = 9;
        private static readonly Fixed LIFESPAN = 5; // seconds
        /// <summary>Max rotation per frame (radians) when steering towards the aiming direction.</summary>
        private static readonly Fixed ROTATION_MAX_STEP = 0.04;

        public static readonly Fixed AOE_RADIUS = 1;

        private Tank shotFromTank;
        private CircleCollider circleCollider;
        private CircleCollider aoeCollider;
        private Rigidbody rigidbody;

        private bool initCollidingWithTank;
        private List<Entity> oldAoeCollisions = new();
        private List<Entity> aoeCollisions = new();
        /// <summary>Distance travelled since launch (for GameRules.MissileArmingDistance).</summary>
        private Fixed travelled;

        /// <summary>The missile has travelled GameRules.MissileArmingDistance: a collision makes it explode.</summary>
        public bool Armed => travelled >= GameRules.Current.MissileArmingDistance;

        /// <summary>The missile collided before being armed and disappears without damage (for the view).</summary>
        public bool Fizzled { get; private set; }

        /// <summary>Damage already dealt by this missile to each entity (it can collide with several in one frame).</summary>
        private readonly Dictionary<Entity, Fixed> damageDealt = new();

        public Missile(FixedVector2 position, Tank shotFromTank) : base(position)
        {
            this.shotFromTank = shotFromTank;

            circleCollider = new CircleCollider(FixedVector2.Zero, RADIUS);
            circleCollider.Layer = CollisionLayer.Missile;
            aoeCollider = new CircleCollider(FixedVector2.Zero, AOE_RADIUS);
            aoeCollider.IsTrigger = true;

            rigidbody = new Rigidbody(new CompositeCollider(circleCollider, aoeCollider), invMass: 3, invInertia: 0.3, friction: 0);

            circleCollider.OnCollision += OnCollision;
            aoeCollider.OnCollision += OnAoeCollision;

            Initialize(circleCollider, aoeCollider, rigidbody);

            Rotation = shotFromTank.LastDirection.Angle();

            if (shotFromTank.TryGetComponent<Collider>(out Collider coll))
                initCollidingWithTank = coll.Colliding(circleCollider);

            rigidbody.Velocity = FixedVector2.FromAngle(Rotation) * SPEED;

            AddBehavior(new Timer(LIFESPAN, null, () => GameplayManager.Destroy(this)));
        }

        public override void Update()
        {
            base.Update();

            oldAoeCollisions = aoeCollisions;
            aoeCollisions = new();

            if (initCollidingWithTank && shotFromTank.TryGetComponent<Collider>(out Collider coll) && !coll.Colliding(circleCollider))
                initCollidingWithTank = false;

            if (GameRules.Current.MissileGuided)
            {
                Fixed goalRotation = shotFromTank.LastDirection.Angle();
                Fixed step = FixedMath.DeltaAngle(Rotation, goalRotation);

                Rotation += FixedMath.Clamp(step, -ROTATION_MAX_STEP, ROTATION_MAX_STEP);
            }

            rigidbody.Velocity = FixedVector2.FromAngle(Rotation) * SPEED;
            travelled += SPEED * GameplayConstants.DELTA_TIME;
        }

        private void OnAoeCollision(CollisionEvent collisionContact)
        {
            aoeCollisions.Add(collisionContact.Other.ParentEntity);
        }

        private void OnCollision(CollisionEvent collisionContact)
        {
            if (initCollidingWithTank && collisionContact.Other.ParentEntity == shotFromTank)
                return; //we want the missile to be able to collide with the original player

            // Not armed yet (GameRules.MissileArmingDistance): no damage, the missile just disappears (once)
            if (Fizzled)
                return;
            if (!Armed)
            {
                Fizzled = true;
                GameplayManager.Destroy(this);
                return;
            }

            if (collisionContact.Other.ParentEntity is Wall wall)
                wall.OnHit?.Invoke(collisionContact.Contacts[0].Position, 5000);

            // The entity hit takes the direct damage, every other entity in the blast (this frame or the previous one)
            // the area damage, and shields in the blast are destroyed. The missile can collide with several entities in
            // the same frame (a tank and a wall, a bullet or a shield): each entity takes at most once the larger of the
            // two damages. Before, the area damage was dealt once per collision and once per frame of the blast list,
            // so a tank could lose 800 to 1 400 health to one missile.
            Entity hit = collisionContact.Other.ParentEntity;
            DealAtLeast(hit, GameRules.Current.MissileDirectDamage);

            foreach (Entity entity in aoeCollisions.Concat(oldAoeCollisions).Distinct())
            {
                if (entity is Shield && entity.TryGetComponent(out Health shieldHealth))
                {
                    if (!shieldHealth.IsDead)
                        shieldHealth.Kill();
                }
                else if (entity != hit)
                    DealAtLeast(entity, GameRules.Current.MissileAreaDamage);
            }

            GameplayManager.Destroy(this);
        }

        /// <summary>Brings the damage dealt by this missile to `entity` up to `damage` (nothing if it is already dead).</summary>
        private void DealAtLeast(Entity entity, Fixed damage)
        {
            if (!entity.TryGetComponent(out Health health) || health.IsDead)
                return;
            damageDealt.TryGetValue(entity, out Fixed dealt);
            if (damage <= dealt)
                return;
            health.Damage(damage - dealt);
            damageDealt[entity] = damage;
        }

        public override void OnDestroyed()
        {
            //technically doing this isn't necessary since it's not a memory leak since circleCollider gets destroyed along with the entity
            //better safe than sound ig
            circleCollider.OnCollision -= OnCollision;
            aoeCollider.OnCollision -= OnAoeCollision;
            base.OnDestroyed();
        }

        public override State GetState()
        {
            return new MissileState()
            {
                Owner = shotFromTank?.Team.ToString(),
                Position = this.Position,
                Radius = RADIUS,
                Rotation = this.Rotation,
                Velocity = rigidbody.Velocity,
                Armed = Armed,
            };
        }
    }
}

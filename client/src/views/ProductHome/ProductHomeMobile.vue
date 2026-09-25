<template>
  <div class="product-home-mobile">
    <div class="paper-grid" aria-hidden="true"></div>
    <div class="ember-orb ember-orb--one" aria-hidden="true"></div>
    <div class="ember-orb ember-orb--two" aria-hidden="true"></div>

    <header class="landing-header">
      <a :href="SPARKARC_GITHUB_URL" target="_blank" rel="noopener" class="landing-brand">
        <AppBrand :size="27" />
      </a>
      <button type="button" class="header-cta" @click="router.push('/login')">
        {{ t('productHomeMobile.header.enterWorkspace') }}
        <ArrowRight :size="15" />
      </button>
    </header>

    <main class="landing-main">
      <section class="hero-section">
        <span class="eyebrow">SparkArc · 2025</span>
        <h1>{{ t('productHomeMobile.hero.title') }}</h1>
        <p class="hero-desc">{{ t('productHomeMobile.hero.desc') }}</p>
        <div class="hero-actions">
          <button type="button" class="primary-cta" @click="router.push('/login')">
            {{ t('productHomeMobile.hero.start') }}
            <ArrowRight :size="17" />
          </button>
          <a class="text-cta" href="#mobile-features">
            {{ t('productHomeDesktop.nav.features') }}
          </a>
        </div>

        <div class="hero-blueprint" aria-hidden="true">
          <div class="blueprint-caption">
            <span class="caption-dot"></span>
            <span>{{ t('productHomeDesktop.nav.workflow') }}</span>
          </div>
          <div class="blueprint-flow">
            <div class="blueprint-node">
              <Lightbulb :size="18" />
              <span>{{ t('mobileFlow.steps.muse') }}</span>
            </div>
            <span class="blueprint-line"></span>
            <div class="blueprint-node">
              <Globe2 :size="18" />
              <span>{{ t('mobileFlow.steps.world') }}</span>
            </div>
            <span class="blueprint-line"></span>
            <div class="blueprint-node blueprint-node--accent">
              <SquarePen :size="18" />
              <span>{{ t('mobileFlow.steps.production') }}</span>
            </div>
          </div>
          <div class="blueprint-foot">
            <span>01</span>
            <span>{{ t('productHomeDesktop.workflow.title') }}</span>
            <span>∞</span>
          </div>
        </div>
      </section>

      <section id="mobile-features" class="feature-section">
        <div class="section-heading">
          <span class="eyebrow">{{ t('productHomeDesktop.nav.features') }}</span>
          <h2>{{ t('productHomeMobile.features.title') }}</h2>
        </div>
        <div class="feature-list">
          <article class="feature-card feature-card--primary">
            <div class="feature-icon"><Users :size="19" /></div>
            <div>
              <h3>{{ t('productHomeMobile.features.items.collab.title') }}</h3>
              <p>{{ t('productHomeMobile.features.items.collab.desc') }}</p>
            </div>
            <span class="feature-number">01</span>
          </article>
          <article class="feature-card">
            <div class="feature-icon"><Globe2 :size="19" /></div>
            <div>
              <h3>{{ t('productHomeMobile.features.items.world.title') }}</h3>
              <p>{{ t('productHomeMobile.features.items.world.desc') }}</p>
            </div>
            <span class="feature-number">02</span>
          </article>
          <article class="feature-card">
            <div class="feature-icon"><Boxes :size="19" /></div>
            <div>
              <h3>{{ t('productHomeMobile.features.items.engine.title') }}</h3>
              <p>{{ t('productHomeMobile.features.items.engine.desc') }}</p>
            </div>
            <span class="feature-number">03</span>
          </article>
        </div>
      </section>

      <section class="workflow-section">
        <div class="section-heading">
          <span class="eyebrow">{{ t('productHomeDesktop.nav.workflow') }}</span>
          <h2>{{ t('productHomeDesktop.workflow.title') }}</h2>
        </div>
        <div class="workflow-list">
          <div class="workflow-item">
            <span class="workflow-index">01</span>
            <div>
              <strong>{{ t('productHomeDesktop.workflow.steps.spark.title') }}</strong>
              <p>{{ t('productHomeDesktop.workflow.steps.spark.desc') }}</p>
            </div>
          </div>
          <div class="workflow-item">
            <span class="workflow-index">02</span>
            <div>
              <strong>{{ t('productHomeDesktop.workflow.steps.fission.title') }}</strong>
              <p>{{ t('productHomeDesktop.workflow.steps.fission.desc') }}</p>
            </div>
          </div>
        </div>
      </section>

      <section class="final-cta">
        <span class="final-mark">✦</span>
        <h2>{{ t('productHomeMobile.hero.title') }}</h2>
        <p>{{ t('productHomeMobile.hero.desc') }}</p>
        <button type="button" class="primary-cta" @click="router.push('/login')">
          {{ t('productHomeMobile.hero.start') }}
          <ArrowRight :size="17" />
        </button>
      </section>
    </main>

    <footer class="landing-footer">
      <a :href="SPARKARC_GITHUB_URL" target="_blank" rel="noopener" class="footer-brand">
        <AppBrand :size="22" :show-text="false" />
        <span>SparkArc</span>
      </a>
      <p>{{ t('productHomeMobile.footer.instanceDisclaimer') }}</p>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { ArrowRight, Boxes, Globe2, Lightbulb, SquarePen, Users } from '@lucide/vue';
import AppBrand from '@/components/share/AppBrand.vue';
import { SPARKARC_GITHUB_URL } from '@/config';

const router = useRouter();
const { t } = useI18n();
</script>

<style scoped>
.product-home-mobile {
  --landing-paper: #faf6ee;
  --landing-paper-deep: #f0e8d8;
  --landing-ink: #2a2420;
  --landing-muted: #71675d;
  --landing-ember: #df7848;
  --landing-border: rgba(42, 36, 32, 0.13);
  position: relative;
  min-height: 100dvh;
  overflow: hidden;
  color: var(--landing-ink);
  background: var(--landing-paper);
  font-family: var(--spark-font);
  color-scheme: light;
}

.paper-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.4;
  background-image: linear-gradient(rgba(74, 103, 65, 0.07) 1px, transparent 1px), linear-gradient(90deg, rgba(74, 103, 65, 0.07) 1px, transparent 1px);
  background-size: 26px 26px;
  mask-image: linear-gradient(to bottom, #000 0%, transparent 48%);
}

.ember-orb {
  position: absolute;
  width: 180px;
  height: 180px;
  border-radius: 50%;
  pointer-events: none;
  filter: blur(2px);
  opacity: 0.38;
  background: radial-gradient(circle at 35% 35%, #f7d98a, transparent 62%);
}
.ember-orb--one { top: 95px; right: -72px; }
.ember-orb--two { top: 430px; left: -120px; opacity: 0.18; }

.landing-header,
.landing-main,
.landing-footer {
  position: relative;
  z-index: 1;
}

.landing-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: calc(var(--sat, 0px) + 14px) 18px 12px;
  border-bottom: 1px solid var(--landing-border);
  background: rgba(250, 246, 238, 0.82);
  backdrop-filter: blur(14px);
}

.landing-brand {
  color: var(--landing-ink);
  text-decoration: none;
  line-height: 1;
}

.header-cta,
.primary-cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid var(--landing-ember);
  border-radius: 999px;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.header-cta {
  min-height: 36px;
  padding: 0 13px;
  color: var(--landing-ember);
  background: transparent;
  font-size: 12px;
}

.landing-main {
  display: block;
  flex: none;
  overflow: visible;
  padding: 34px 18px 0;
}

.hero-section {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.eyebrow {
  color: var(--landing-ember);
  font-family: var(--spark-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.15em;
  text-transform: uppercase;
}

.hero-section h1 {
  max-width: 10em;
  margin: 13px 0 11px;
  font-size: clamp(2.6rem, 12vw, 4.2rem);
  font-weight: 500;
  letter-spacing: -0.06em;
  line-height: 1.02;
}

.hero-desc {
  max-width: 31rem;
  margin: 0;
  color: var(--landing-muted);
  font-size: 15px;
  line-height: 1.65;
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 17px;
  margin-top: 22px;
}

.primary-cta {
  min-height: 45px;
  padding: 0 17px;
  color: #fff;
  background: var(--landing-ember);
  box-shadow: 0 5px 0 #be5d34, 0 12px 20px rgba(223, 120, 72, 0.2);
}

.primary-cta:active { transform: translateY(1px); }

.text-cta {
  color: var(--landing-ink);
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  border-bottom: 1px solid currentColor;
}

.hero-blueprint {
  width: 100%;
  margin-top: 34px;
  padding: 15px 14px 12px;
  border: 1px solid var(--landing-border);
  background: rgba(255, 255, 255, 0.38);
  box-shadow: 0 14px 35px rgba(74, 55, 33, 0.08);
}

.blueprint-caption,
.blueprint-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--landing-muted);
  font-family: var(--spark-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.caption-dot {
  width: 6px;
  height: 6px;
  margin-right: auto;
  border-radius: 50%;
  background: var(--landing-ember);
  box-shadow: 0 0 0 4px rgba(223, 120, 72, 0.14);
}

.blueprint-flow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 23px 0 19px;
}

.blueprint-node {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  color: #556b50;
  font-size: 11px;
  font-weight: 700;
}

.blueprint-node--accent { color: var(--landing-ember); }

.blueprint-line {
  height: 1px;
  flex: 1;
  background: repeating-linear-gradient(90deg, rgba(42, 36, 32, 0.22) 0 4px, transparent 4px 8px);
}

.feature-section,
.workflow-section {
  margin-top: 65px;
}

.section-heading h2 {
  margin: 7px 0 17px;
  font-size: 25px;
  font-weight: 500;
  letter-spacing: -0.04em;
}

.feature-list,
.workflow-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.feature-card {
  position: relative;
  display: grid;
  grid-template-columns: 38px 1fr auto;
  align-items: start;
  gap: 12px;
  padding: 15px 13px;
  border: 1px solid var(--landing-border);
  background: rgba(255, 255, 255, 0.5);
}

.feature-card--primary {
  border-color: rgba(223, 120, 72, 0.44);
  background: linear-gradient(135deg, rgba(255, 249, 232, 0.96), rgba(255, 255, 255, 0.5));
}

.feature-icon {
  width: 36px;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--landing-ember);
  border: 1px solid rgba(223, 120, 72, 0.34);
  border-radius: 50%;
  background: rgba(223, 120, 72, 0.09);
}

.feature-card h3 {
  margin: 1px 0 5px;
  font-size: 16px;
  font-weight: 700;
}

.feature-card p {
  margin: 0;
  color: var(--landing-muted);
  font-size: 13px;
  line-height: 1.5;
}

.feature-number,
.workflow-index {
  color: var(--landing-muted);
  font-family: var(--spark-mono);
  font-size: 10px;
  opacity: 0.7;
}

.workflow-item {
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 12px;
  padding: 12px 0 14px;
  border-bottom: 1px solid var(--landing-border);
}

.workflow-item strong {
  font-size: 15px;
  font-weight: 700;
}

.workflow-item p {
  margin: 5px 0 0;
  color: var(--landing-muted);
  font-size: 13px;
  line-height: 1.55;
}

.final-cta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  margin: 68px -18px 0;
  padding: 29px 18px 37px;
  background: var(--landing-paper-deep);
}

.final-mark {
  color: var(--landing-ember);
  font-size: 24px;
}

.final-cta h2 {
  margin: 8px 0 7px;
  font-size: 27px;
  font-weight: 500;
}

.final-cta p {
  margin: 0 0 19px;
  color: var(--landing-muted);
  font-size: 13px;
  line-height: 1.55;
}

.landing-footer {
  padding: 20px 18px calc(var(--sab, 0px) + 22px);
  color: var(--landing-muted);
  background: var(--landing-paper-deep);
  font-size: 11px;
}

.footer-brand {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--landing-ink);
  font-size: 15px;
  font-weight: 700;
  text-decoration: none;
}

.landing-footer p {
  max-width: 32rem;
  margin: 10px 0 0;
  line-height: 1.55;
}

@media (min-width: 560px) {
  .landing-main { max-width: 560px; margin: 0 auto; }
  .landing-header { padding-left: max(18px, calc((100vw - 560px) / 2)); padding-right: max(18px, calc((100vw - 560px) / 2)); }
}
</style>

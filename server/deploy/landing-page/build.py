#!/usr/bin/env python3
"""Fabrique la page d'accueil du site en français (index.html) et en anglais (index.en.html) à partir d'UN seul modèle.

Les textes à traduire s'écrivent [[français||anglais]] dans PAGE ; tout le reste (styles, structure, images) est commun, donc les deux
versions ne divergent plus. Usage : python build.py   (depuis ce dossier ou n'importe où ; réécrit index.html et index.en.html).
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL_SITE = "https://waseboard.salsi.bid"
URL_RELEASE = "https://github.com/salsi64/WaseBoard/releases/latest"
URL_DISCORD = "https://discord.gg/HAGTNGFyQd"
URL_INVITE = "https://discord.com/oauth2/authorize?client_id=1546838877631811656&amp;permissions=3146752&amp;scope=bot%20applications.commands"
URL_GITHUB = "https://github.com/salsi64/WaseBoard"

PAGE = r"""<!DOCTYPE html>
<html lang="[[fr||en]]">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WaseBoard</title>
<meta name="description" content="[[Soundboard partagé pour Discord : cliquez un son, il joue sur vos enceintes et dans le vocal, pour tout le monde. Application Windows, version web et téléphone.||A shared soundboard for Discord: click a sound and it plays on your speakers and in voice chat, for everyone. Windows app, web version and phone.]]">
<meta name="theme-color" content="#0b1220">
<link rel="alternate" hreflang="fr" href="/">
<link rel="alternate" hreflang="en" href="/en">
<meta property="og:title" content="WaseBoard">
<meta property="og:description" content="[[Le soundboard partagé de votre vocal Discord.||The shared soundboard for your Discord voice channel.]]">
<meta property="og:image" content="URL_SITE/img/[[hero-flat-3.4.png||hero-flat-3.4-en.png]]">
<style>
  /* Une seule ambiance (nuit bleue de l'application) ; les accents reprennent ceux des boutons de son : bleu, rose, vert, ambre. */
  :root {
    color-scheme: dark;
    --bg: #0b1220;
    --bg-2: #0f1830;
    --panel: #131d33;
    --panel-hi: #1a2745;
    --line: #24324f;
    --text: #eaf0fa;
    --dim: #a3b2cc;
    --accent: #38bdf8;
    --pink: #f472b6;
    --green: #34d399;
    --amber: #fbbf24;
    --on-accent: #08121f;
    --radius: 16px;
    --font: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
    --font-display: "Segoe UI Variable Display", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; scroll-padding-top: 72px; }
  body {
    margin: 0; background: var(--bg); color: var(--text); font-family: var(--font); font-size: 16px; line-height: 1.6;
    background-image:
      radial-gradient(900px 420px at 80% -80px, rgba(56, 189, 248, .14), transparent 70%),
      radial-gradient(700px 360px at 0% 320px, rgba(244, 114, 182, .08), transparent 70%);
    background-repeat: no-repeat;
  }
  img { max-width: 100%; height: auto; }
  a { color: var(--accent); }
  code { background: var(--panel-hi); padding: 1px 7px; border-radius: 6px; font-size: .9em; }
  .wrap { max-width: 1140px; margin: 0 auto; padding-inline: max(20px, env(safe-area-inset-left, 0px)) max(20px, env(safe-area-inset-right, 0px)); }
  h1, h2, h3 { font-family: var(--font-display); letter-spacing: -.02em; line-height: 1.15; text-wrap: balance; margin: 0; }
  h2 { font-size: clamp(1.5rem, 3.2vw, 2.1rem); }
  h3 { font-size: 1.1rem; letter-spacing: -.01em; }
  p { margin: 0; }
  .eyebrow { color: var(--accent); font-weight: 700; font-size: .8rem; letter-spacing: .12em; text-transform: uppercase; margin-bottom: 10px; }
  .lead { color: var(--dim); font-size: 1.08rem; max-width: 56ch; }

  /* Barre du haut */
  .nav { position: sticky; top: 0; z-index: 10; background: rgba(11, 18, 32, .82); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border-bottom: 1px solid var(--line); }
  .nav .wrap { display: flex; align-items: center; gap: 22px; min-height: 60px; }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 1.15rem; color: var(--text); text-decoration: none; margin-right: auto; }
  .brand span { font-size: 1.5rem; }
  .nav a.link { color: var(--dim); text-decoration: none; font-weight: 600; font-size: .95rem; }
  .nav a.link:hover { color: var(--text); }
  .nav .lang { border: 1px solid var(--line); border-radius: 999px; padding: 3px 11px; font-size: .85rem; }

  /* Boutons */
  .btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 9px; padding: 13px 22px; border-radius: 12px; font-weight: 700; font-size: 1rem;
    text-decoration: none; border: 1px solid transparent; transition: transform .12s ease, filter .12s ease, background .12s ease;
  }
  .btn:hover { transform: translateY(-1px); filter: brightness(1.08); }
  .btn:focus-visible, .nav a:focus-visible, a.card-link:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
  .btn.primary { background: var(--accent); color: var(--on-accent); }
  .btn.web { background: linear-gradient(135deg, var(--green), var(--accent)); color: var(--on-accent); }
  .btn.ghost { background: var(--panel); color: var(--text); border-color: var(--line); }
  .btn.small { padding: 8px 16px; font-size: .9rem; border-radius: 10px; }
  .btn.big { padding: 14px 22px; font-size: 1rem; }

  /* En-tête */
  .hero { padding-block: 56px 28px; }
  .hero .wrap { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.2fr); gap: 40px; align-items: center; }
  .hero h1 { font-size: clamp(2.1rem, 5vw, 3.3rem); font-weight: 800; }
  .hero h1 em { font-style: normal; background: linear-gradient(120deg, var(--accent), var(--pink)); -webkit-background-clip: text; background-clip: text; color: transparent; }
  .hero .lead { margin-top: 18px; }
  .cta { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; padding: 0; list-style: none; }
  .chips li { color: var(--dim); font-size: .88rem; border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px; }
  .shot { border-radius: 14px; border: 1px solid var(--line); box-shadow: 0 30px 80px -30px rgba(0, 0, 0, .8), 0 0 0 1px rgba(255, 255, 255, .03); display: block; width: 100%; }
  .also { margin-top: 14px; color: var(--dim); font-size: .92rem; }

  /* Sections */
  section { padding-block: 56px; }
  .head { display: grid; gap: 4px; margin-bottom: 30px; }

  .steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; counter-reset: step; padding: 0; margin: 0; list-style: none; }
  .steps li { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 22px; counter-increment: step; }
  .steps li::before {
    content: counter(step); display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%; margin-bottom: 14px;
    background: var(--accent); color: var(--on-accent); font-weight: 800;
  }
  .steps li:nth-child(2)::before { background: var(--pink); }
  .steps li:nth-child(3)::before { background: var(--green); }
  .steps p { color: var(--dim); margin-top: 6px; font-size: .96rem; }
  .steps .more { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 8px; }

  .web-panel {
    display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 40px; align-items: center; padding: 38px;
    background: linear-gradient(135deg, rgba(52, 211, 153, .12), rgba(56, 189, 248, .10)), var(--panel); border: 1px solid var(--line); border-radius: 24px;
  }
  .web-panel ul { margin: 20px 0 26px; padding: 0; list-style: none; display: grid; gap: 10px; }
  .web-panel li { padding-left: 30px; position: relative; color: var(--dim); }
  .web-panel li::before { content: "✓"; position: absolute; left: 0; color: var(--green); font-weight: 800; }
  .web-panel li strong { color: var(--text); }
  .phone { justify-self: center; width: min(100%, 300px); border-radius: 36px; padding: 10px; background: #05080f; border: 1px solid var(--line); box-shadow: 0 30px 70px -28px rgba(0, 0, 0, .9); }
  .phone img { display: block; width: 100%; border-radius: 27px; }

  .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
  .feature { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 22px; }
  .feature .ico { font-size: 1.7rem; line-height: 1; display: block; margin-bottom: 12px; }
  .feature p { color: var(--dim); margin-top: 6px; font-size: .95rem; }
  .feature a { color: var(--accent); font-weight: 600; text-decoration: none; display: inline-block; margin-top: 10px; font-size: .92rem; }
  .feature a:hover { text-decoration: underline; }

  .spot { display: grid; grid-template-columns: minmax(0, 280px) minmax(0, 1fr); gap: 36px; align-items: center; margin-top: 22px; padding: 28px;
    border: 1px solid var(--line); border-radius: 22px; background: var(--panel); }
  .spot.flip { grid-template-columns: minmax(0, 1fr) minmax(0, 280px); }
  .spot.flip .shot-wrap { order: 2; }
  .spot img { border-radius: 14px; border: 1px solid var(--line); display: block; margin: 0 auto; }
  .spot p { color: var(--dim); margin-top: 10px; }
  .spot .btn { margin-top: 18px; }
  .spot .tag { display: inline-block; font-size: .75rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--amber); border: 1px solid var(--amber); border-radius: 999px; padding: 2px 10px; margin-bottom: 12px; }

  .hosts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .host { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 26px; display: flex; flex-direction: column; }
  .host.rec { border-color: var(--accent); box-shadow: 0 0 0 1px rgba(56, 189, 248, .25); }
  .host .badge { align-self: flex-start; background: var(--accent); color: var(--on-accent); font-size: .72rem; font-weight: 800; letter-spacing: .06em; padding: 3px 10px; border-radius: 999px; margin-bottom: 12px; }
  .host ul { padding-left: 20px; color: var(--dim); margin: 12px 0 22px; display: grid; gap: 4px; }
  .host .btn { margin-top: auto; align-self: flex-start; }
  .diagram { width: 100%; max-width: 640px; height: auto; margin: 0 auto 26px; display: block; }
  .diagram .box { fill: var(--panel-hi); stroke: var(--line); }
  .diagram text { fill: var(--text); font-family: inherit; font-size: 13px; }
  .diagram .sub { fill: var(--dim); font-size: 11px; }
  .diagram .flow { stroke: var(--accent); stroke-width: 2; stroke-dasharray: 5 5; fill: none; animation: dash 1.2s linear infinite; }
  @keyframes dash { to { stroke-dashoffset: -20; } }

  .final { text-align: center; padding-block: 64px; border-block: 1px solid var(--line); background: radial-gradient(600px 240px at 50% 0, rgba(56, 189, 248, .12), transparent 70%), var(--bg-2); }
  .final .cta { justify-content: center; }
  .final .lead { margin: 12px auto 0; }

  footer { padding-block: 34px 56px; color: var(--dim); font-size: .9rem; text-align: center; }
  footer a { color: var(--dim); }
  footer .links { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px 18px; margin-top: 10px; }

  @media (max-width: 860px) {
    .hero .wrap, .web-panel, .spot, .spot.flip { grid-template-columns: minmax(0, 1fr); }
    .spot.flip .shot-wrap { order: 0; }
    .steps, .grid, .hosts { grid-template-columns: minmax(0, 1fr); }
    .hero { padding-block: 36px 12px; }
    .web-panel { padding: 26px 20px; }
    .nav .link { display: none; }
    .nav .wrap { gap: 12px; }
  }
  @media (max-width: 520px) { .nav .lang { display: none; } .nav .btn.small { padding: 8px 12px; } .cta .btn { flex: 1 1 100%; } .final .cta .btn { flex: 1 1 100%; } }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } .btn { transition: none; } .diagram .flow { animation: none; } }
</style>
</head>
<body>
  <nav class="nav" aria-label="[[Navigation||Navigation]]">
    <div class="wrap">
      <a class="brand" href="[[/||/en]]"><span aria-hidden="true">🎛️</span> WaseBoard</a>
      <a class="link" href="#demarrer">[[Démarrer||Get started]]</a>
      <a class="link" href="#web">[[Version web||Web version]]</a>
      <a class="link" href="#fonctions">[[Fonctions||Features]]</a>
      <a class="link" href="#heberger">[[Héberger||Host]]</a>
      <a class="link lang" href="[[/en||/]]" hreflang="[[en||fr]]">[[English||Français]]</a>
      <a class="btn web small" href="/app/">🌐 [[Utiliser sur le web||Use on the web]]</a>
    </div>
  </nav>

  <header class="hero">
    <div class="wrap">
      <div>
        <p class="eyebrow">[[Soundboard partagé pour Discord||Shared soundboard for Discord]]</p>
        <h1>[[Cliquez un son. <em>Tout le vocal l'entend.</em>||Click a sound. <em>The whole channel hears it.</em>]]</h1>
        <p class="lead">[[WaseBoard joue vos sons sur vos enceintes et dans votre salon vocal Discord, pour tout le monde, sans jamais passer par votre micro.||WaseBoard plays your sounds on your speakers and in your Discord voice channel, for everyone, without ever going through your microphone.]]</p>
        <div class="cta">
          <a class="btn primary big" href="URL_RELEASE">⬇ [[Télécharger pour Windows||Download for Windows]]</a>
          <a class="btn web big" href="/app/">🌐 [[Utiliser sur le web||Use on the web]]</a>
        </div>
        <p class="also">[[Rien à installer avec la version web : ordinateur, tablette ou téléphone.||Nothing to install with the web version: computer, tablet or phone.]]
          <a href="URL_INVITE">[[Ajouter le bot à mon serveur||Add the bot to my server]]</a></p>
        <ul class="chips" aria-label="[[Points clés||Key points]]">
          <li>[[Gratuit||Free]]</li><li>[[Open source||Open source]]</li><li>[[Windows · Web · Téléphone||Windows · Web · Phone]]</li><li>[[Français et anglais||English and French]]</li>
        </ul>
      </div>
      <img class="shot" src="img/[[hero-flat-3.4.png||hero-flat-3.4-en.png]]" width="1760" height="1056"
           alt="[[Capture d'écran de WaseBoard : boutons de son en pilules colorées avec leur forme d'onde, catégories repliables, bouton Clip, barre latérale avec les serveurs Discord et les personnes qui jouent un son en ce moment||Screenshot of WaseBoard: sound buttons as colored pills with their waveform, collapsible categories, the Clip button, a sidebar with the Discord servers and the people playing a sound right now]]">
    </div>
  </header>

  <section id="demarrer">
    <div class="wrap">
      <div class="head">
        <p class="eyebrow">[[Démarrer||Get started]]</p>
        <h2>[[Prêt en trois étapes||Ready in three steps]]</h2>
      </div>
      <ol class="steps">
        <li>
          <h3>[[Installez ou ouvrez||Install or open]]</h3>
          <p>[[Téléchargez l'application Windows, ou ouvrez simplement la version web : c'est le même catalogue de sons.||Download the Windows app, or simply open the web version: it is the same sound catalog.]]</p>
          <div class="more"><a class="btn primary small" href="URL_RELEASE">⬇ Windows</a><a class="btn web small" href="/app/">🌐 Web</a></div>
        </li>
        <li>
          <h3>[[Connectez-vous avec Discord||Sign in with Discord]]</h3>
          <p>[[Votre compte suffit : il sert à afficher votre avatar et à retrouver votre salon vocal. Rien d'autre à saisir.||Your account is enough: it is used to show your avatar and find your voice channel. Nothing else to type.]]</p>
          <div class="more"><a class="btn ghost small" href="URL_INVITE">➕ [[Le bot n'est pas sur mon serveur||The bot is not on my server]]</a></div>
        </li>
        <li>
          <h3>[[Rejoignez le vocal, cliquez||Join voice, click]]</h3>
          <p>[[Entrez dans un salon vocal, cliquez « 🔊 Rejoindre mon vocal » (ou tapez /join), puis cliquez un son : tout le monde l'entend.||Join a voice channel, click “🔊 Join my voice channel” (or type /join), then click a sound: everyone hears it.]]</p>
        </li>
      </ol>
    </div>
  </section>

  <section id="web">
    <div class="wrap">
      <div class="web-panel">
        <div>
          <p class="eyebrow">[[Nouveau · version web||New · web version]]</p>
          <h2>[[WaseBoard dans votre navigateur||WaseBoard in your browser]]</h2>
          <ul>
            <li><strong>[[Aucune installation||Nothing to install]]</strong> : [[ouvrez la page, connectez-vous avec Discord, c'est prêt.||open the page, sign in with Discord, and you're set.]]</li>
            <li><strong>[[Sur téléphone||On a phone]]</strong> : [[« Ajouter à l'écran d'accueil » et WaseBoard se comporte comme une appli.||“Add to home screen” and WaseBoard behaves like an app.]]</li>
            <li><strong>[[Tout y est||Everything is there]]</strong> : [[jouer, arrêter, favoris, couleurs, et même ajouter ou découper des sons.||play, stop, favorites, colors, and even adding or trimming sounds.]]</li>
          </ul>
          <a class="btn web big" href="/app/">🌐 [[Ouvrir la version web||Open the web version]]</a>
        </div>
        <div class="phone"><img src="img/[[web-phone.png||web-phone-en.png]]" width="780" height="1520" loading="lazy"
          alt="[[La version web sur téléphone : recherche, serveurs, sons en pilules colorées, bouton Rejoindre mon vocal||The web version on a phone: search, servers, sounds as colored pills, the Join my voice channel button]]"></div>
      </div>
    </div>
  </section>

  <section id="fonctions">
    <div class="wrap">
      <div class="head">
        <p class="eyebrow">[[Fonctions||Features]]</p>
        <h2>[[Tout ce qu'il faut pour un vocal qui s'amuse||Everything for a voice channel that has fun]]</h2>
      </div>
      <div class="grid">
        <article class="feature"><span class="ico" aria-hidden="true">🎞</span><h3>[[Clip des dernières secondes||Last-seconds clip]]</h3><p>[[Un raccourci garde ce qui vient de passer sur votre PC (jeu, vidéo, Discord) et en fait un son.||A shortcut keeps what just played on your PC (game, video, Discord) and turns it into a sound.]]</p></article>
        <article class="feature"><span class="ico" aria-hidden="true">✏️</span><h3>[[Éditeur de sons||Sound editor]]</h3><p>[[Nom, emoji, couleur, volume de 0 à 200 %, découpe sur la forme d'onde, remplacement du fichier.||Name, emoji, color, volume from 0 to 200%, trimming on the waveform, file replacement.]]</p></article>
        <article class="feature"><span class="ico" aria-hidden="true">⌨️</span><h3>[[Commandes Discord||Discord commands]]</h3><p>[[<code>/play</code> avec recherche, <code>/stop</code>, <code>/top</code> et <code>/bilan</code> : sans ouvrir l'application.||<code>/play</code> with search, <code>/stop</code>, <code>/top</code> and <code>/bilan</code>: without opening the app.]]</p></article>
        <article class="feature"><span class="ico" aria-hidden="true">🎮</span><h3>[[Micro en jeu||In-game mic]]</h3><p>[[Vos sons passent aussi dans votre micro : vos coéquipiers les entendent dans Valorant, CS2, Fortnite…||Your sounds also go through your microphone: your teammates hear them in Valorant, CS2, Fortnite…]]</p><a href="/micro-en-jeu.html">[[Découvrir →||Discover (in French) →]]</a></article>
        <article class="feature"><span class="ico" aria-hidden="true">🗂</span><h3>[[Plusieurs serveurs||Several servers]]</h3><p>[[Un catalogue par serveur Discord, des catégories perso, des favoris, des raccourcis clavier globaux.||One catalog per Discord server, personal categories, favorites, global keyboard shortcuts.]]</p></article>
        <article class="feature"><span class="ico" aria-hidden="true">🌍</span><h3>[[Français et anglais||English and French]]</h3><p>[[L'application suit la langue de Windows ; thèmes clair, sombre, Flat et palettes de couleurs au choix.||The app follows your Windows language; light and dark themes, Flat style and color palettes to choose from.]]</p></article>
      </div>

      <div class="spot">
        <div class="shot-wrap"><img src="img/[[clip-trim.png||clip-trim-en.png]]" width="420" height="411" loading="lazy" alt="[[La fenêtre d'ajout ouverte par le clip : nom, découpe sur la forme d'onde||The add window opened by the clip: name, trimming on the waveform]]"></div>
        <div>
          <h3>🎞 [[Un moment drôle ? Gardez-le en un raccourci||Something funny just happened? Keep it with one shortcut]]</h3>
          <p>[[WaseBoard garde en mémoire les 10, 15 ou 30 dernières secondes du son de votre PC. Un raccourci, et la fenêtre d'ajout s'ouvre : vous découpez, vous nommez, c'est un son. Désactivé par défaut ; rien n'est écrit sur le disque avant que vous le demandiez, et un voyant rouge indique que la capture tourne.||WaseBoard holds the last 10, 15 or 30 seconds of your PC's sound in memory. Press a shortcut and the add window opens: trim it, name it, it's a sound. Off by default; nothing is written to disk until you ask, and a red light shows when the capture is running.]]</p>
          <a class="btn ghost small" href="URL_GITHUB/blob/master/docs/clip-dernieres-secondes.md">[[Lire le guide||Read the guide (in French)]]</a>
        </div>
      </div>
      <div class="spot flip">
        <div>
          <span class="tag">[[Expérimental||Experimental]]</span>
          <h3>🎮 [[Vos sons aussi dans le vocal de vos jeux||Your sounds in your games' voice chat too]]</h3>
          <p>[[Avec le micro en jeu, vos sons passent dans votre micro, sans logiciel en plus et sans rien changer dans le jeu. Un bouton 🎮 Jeu / 🎧 Discord choisit où ils vont.||With the in-game mic, your sounds go through your microphone, with no extra software and nothing to change in the game. A 🎮 Game / 🎧 Discord button chooses where they go.]]</p>
          <a class="btn ghost small" href="/micro-en-jeu.html">[[Découvrir le micro en jeu||Discover the in-game mic (in French)]]</a>
        </div>
        <div class="shot-wrap"><img src="img/micro-en-jeu-interrupteur.png" width="370" height="348" loading="lazy" alt="[[L'interrupteur Jeu / Discord de WaseBoard : le mode actif est allumé||WaseBoard's Game / Discord switch: the active mode is lit]]"></div>
      </div>
    </div>
  </section>

  <section id="heberger">
    <div class="wrap">
      <div class="head">
        <p class="eyebrow">[[Vous administrez un serveur Discord ?||Do you run a Discord server?]]</p>
        <h2>[[Comment ça marche, côté serveur||How it works on the server side]]</h2>
        <p class="lead">[[Le bot Discord et l'application se connectent au même endroit : un « serveur WaseBoard », qui héberge le catalogue de sons. Celui de cette page est public et gratuit.||The Discord bot and the app connect to the same place: a “WaseBoard server”, which hosts the sound catalog. The one behind this page is public and free.]]</p>
      </div>
      <svg class="diagram" viewBox="0 0 600 120" xmlns="http://www.w3.org/2000/svg" role="img"
           aria-label="[[Schéma : votre serveur Discord, l'application et la version web se connectent à un serveur WaseBoard central||Diagram: your Discord server, the app and the web version connect to a central WaseBoard server]]">
        <rect class="box" x="8" y="30" width="162" height="60" rx="12"/>
        <text x="89" y="55" text-anchor="middle">💬 [[Votre Discord||Your Discord]]</text>
        <text class="sub" x="89" y="72" text-anchor="middle">[[salon vocal||voice channel]]</text>
        <rect class="box" x="219" y="30" width="162" height="60" rx="12"/>
        <text x="300" y="55" text-anchor="middle">🖥️ [[Serveur||WaseBoard]]</text>
        <text class="sub" x="300" y="72" text-anchor="middle">[[WaseBoard||server]]</text>
        <rect class="box" x="430" y="30" width="162" height="60" rx="12"/>
        <text x="511" y="55" text-anchor="middle">🎛️ [[Application||App]]</text>
        <text class="sub" x="511" y="72" text-anchor="middle">[[Windows · Web||Windows · Web]]</text>
        <path class="flow" d="M170 60 H219"/>
        <path class="flow" d="M381 60 H430"/>
      </svg>
      <div class="hosts">
        <div class="host rec">
          <span class="badge">[[RECOMMANDÉ||RECOMMENDED]]</span>
          <h3>⚡ [[Utiliser l'instance publique||Use the public instance]]</h3>
          <p style="color:var(--dim);margin-top:6px">[[Ajoutez le bot à votre Discord, téléchargez le client, connectez-vous : c'est tout.||Add the bot to your Discord, download the client, sign in: that's all.]]</p>
          <ul><li>[[Gratuit, en ligne dès maintenant||Free, online right now]]</li><li>[[Aucune compétence technique requise||No technical skills required]]</li><li>[[Bibliothèque de sons propre à chaque serveur||A separate sound library for each server]]</li></ul>
          <a class="btn primary" href="URL_INVITE">➕ [[Ajouter à mon serveur||Add to my server]]</a>
        </div>
        <div class="host">
          <h3>🏠 [[Héberger mon propre serveur||Host my own server]]</h3>
          <p style="color:var(--dim);margin-top:6px">[[Open source, sans serveur central imposé : le vôtre, sans limite, avec un contrôle total des données.||Open source, with no central server imposed: yours, with no limit and full control over the data.]]</p>
          <ul><li>[[Assistant Docker guidé (~15 min)||Guided Docker assistant (~15 min)]]</li><li>[[Aucun plafond : c'est votre machine||No ceiling: it's your machine]]</li><li>[[Alertes, sauvegardes et surveillance prévues||Alerts, backups and monitoring included]]</li></ul>
          <a class="btn ghost" href="URL_GITHUB/blob/master/server/README.md">📖 [[Guide d'installation||Installation guide (in French)]]</a>
        </div>
      </div>
    </div>
  </section>

  <div class="final">
    <div class="wrap">
      <h2>[[Prêt à faire entendre vos sons ?||Ready to let your sounds be heard?]]</h2>
      <p class="lead">[[Gratuit, sans compte à créer : votre compte Discord suffit.||Free, no account to create: your Discord account is enough.]]</p>
      <div class="cta">
        <a class="btn primary big" href="URL_RELEASE">⬇ [[Télécharger pour Windows||Download for Windows]]</a>
        <a class="btn web big" href="/app/">🌐 [[Utiliser sur le web||Use on the web]]</a>
        <a class="btn ghost big" href="URL_DISCORD">💬 [[Rejoindre le Discord||Join the Discord]]</a>
      </div>
    </div>
  </div>

  <footer>
    <div class="wrap">
      <p>[[WaseBoard est un projet open source.||WaseBoard is an open source project.]] <a href="URL_GITHUB">GitHub</a></p>
      <div class="links">
        <a href="/privacy.html">[[Confidentialité||Privacy (in French)]]</a>
        <a href="/terms.html">[[Conditions d'utilisation||Terms of use (in French)]]</a>
        <a href="URL_GITHUB/blob/master/docs/FAQ-utilisateurs.md">FAQ</a>
        <a href="URL_GITHUB/releases/tag/v3.4.0">[[Nouveautés de la 3.4||What's new in 3.4]]</a>
        <a href="[[/en||/]]" hreflang="[[en||fr]]">[[English||Français]]</a>
      </div>
    </div>
  </footer>
</body>
</html>
"""

CONSTANTS = {"URL_SITE": URL_SITE, "URL_RELEASE": URL_RELEASE, "URL_DISCORD": URL_DISCORD, "URL_INVITE": URL_INVITE, "URL_GITHUB": URL_GITHUB}
MARK = re.compile(r"\[\[(.*?)\|\|(.*?)\]\]", re.S)


def render(language: str) -> str:
    index = 0 if language == "fr" else 1
    text = MARK.sub(lambda m: m.group(index + 1), PAGE)
    for key, value in CONSTANTS.items():
        text = text.replace(key, value)
    return text


def main() -> int:
    for language, name in (("fr", "index.html"), ("en", "index.en.html")):
        html = render(language)
        if "[[" in html or "||" in html:
            print(f"{name} : marqueur de traduction mal formé", file=sys.stderr)
            return 1
        (HERE / name).write_bytes(html.encode("utf-8"))
        print(f"{name} écrit ({len(html):,} caractères)".replace(",", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())

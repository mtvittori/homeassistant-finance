/**
 * fd-person-card — Single person Spielgeld breakdown card.
 *
 * Properties:
 *   member     {object}  — Household member data
 *   splitModel {string}  — Current split model name
 */

class FdPersonCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._member = null;
    this._splitModel = "proportional";
  }

  set member(v) { this._member = v; this._render(); }
  set splitModel(v) { this._splitModel = v; this._render(); }

  disconnectedCallback() {
    // No timers or observers to clean up in this component.
  }

  _render() {
    const m = this._member;
    if (!m) {
      this.shadowRoot.innerHTML = "";
      return;
    }

    const { SHARED_CSS, escHtml } = window._fd;

    const eur = (v) => new Intl.NumberFormat("de-DE", {
      style: "currency", currency: "EUR",
    }).format(v || 0);

    const splitLabel = window._fd.tSync(
      this._splitModel === "proportional" ? "split.proportional"
      : this._splitModel === "equal" ? "split.equal" : "split.custom"
    );

    const spielgeldClass = m.spielgeld >= 0 ? "pos" : "neg";
    const bonusRow = m.bonus_amount > 0
      ? `<li class="row"><span class="l">Bonus (erkannt)</span><span class="pos">${eur(m.bonus_amount)}</span></li>`
      : "";

    const LOCAL_CSS = `
.person {
  background: var(--sf);
  border: 1px solid var(--bd);
  border-radius: var(--r);
  padding: 20px;
}
.name { font-size: 16px; font-weight: 600; margin-bottom: 2px; }
.ratio { font-size: 12px; color: var(--tx2); margin-bottom: 14px; }
.rows { list-style: none; padding: 0; margin: 0; }
.row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--bd);
}
.row:last-child { border-bottom: none; }
.row .l { color: var(--tx2); }
.saldo {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 2px solid var(--bd);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.saldo .l { font-size: 14px; font-weight: 600; }
.saldo .v { font-size: 22px; font-weight: 700; }
`;

    this.shadowRoot.innerHTML = `
<style>${SHARED_CSS}${LOCAL_CSS}</style>
<div class="person">
  <div class="name">${escHtml(m.person)}</div>
  <div class="ratio">${window._fd.tSync("person.income_ratio", { pct: (m.income_ratio || 0).toFixed(1) })} &middot; ${escHtml(splitLabel)}</div>
  <ul class="rows">
    <li class="row"><span class="l">${window._fd.tSync("person.net_income")}</span><span>${eur(m.net_income)}</span></li>
    <li class="row"><span class="l">${window._fd.tSync("person.shared_share")}</span><span class="neg">${eur(m.shared_costs_share)}</span></li>
    <li class="row"><span class="l">${window._fd.tSync("person.own_expenses")}</span><span class="neg">${eur(m.individual_costs)}</span></li>
    ${bonusRow}
  </ul>
  <div class="saldo">
    <span class="l">${window._fd.tSync("person.pocket_money")}</span>
    <span class="v ${spielgeldClass}">${eur(m.spielgeld)}</span>
  </div>
</div>`;
  }
}

customElements.define("fd-person-card", FdPersonCard);

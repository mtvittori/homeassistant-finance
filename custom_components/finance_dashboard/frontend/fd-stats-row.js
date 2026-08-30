/**
 * fd-stats-row — 4-KPI grid: balance, expenses, income, savings rate.
 *
 * Properties:
 *   data {object} — Unified data object from fd-data-provider
 */

class FdStatsRow extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
  }

  set data(v) { this._data = v; this._render(); }

  _render() {
    const d = this._data;
    if (!d) {
      // Show skeleton cards while data is loading
      this._renderSkeleton();
      return;
    }

    const { SHARED_CSS } = window._fd;

    const eur = (v) => new Intl.NumberFormat("de-DE", {
      style: "currency", currency: "EUR",
    }).format(v || 0);

    const totalBalance = d.totalBalance || 0;
    const totalExp = d.summary?.totalExpenses || 0;
    const totalInc = d.summary?.totalIncome || 0;
    const surplus = d.summary?.balance || 0;
    const txnCount = d.summary?.transactionCount || 0;
    const accountCount = d.accountCount || 0;
    const savingsRate = totalInc > 0 ? Math.round(surplus / totalInc * 100) : 0;

    const LOCAL_CSS = `
:host {
  margin-bottom: 20px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
@media (max-width: 768px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
}
`;

    this.shadowRoot.innerHTML = `
<style>${SHARED_CSS}${LOCAL_CSS}</style>
<div class="stats">
  <fd-stat-card id="balance"></fd-stat-card>
  <fd-stat-card id="expenses"></fd-stat-card>
  <fd-stat-card id="income"></fd-stat-card>
  <fd-stat-card id="savings"></fd-stat-card>
</div>`;

    const balance = this.shadowRoot.getElementById("balance");
    balance.label = window._fd.tSync("stats.balance");
    balance.value = eur(totalBalance);
    balance.subtitle = `${accountCount} ${window._fd.tSync(accountCount === 1 ? "general.accounts_singular" : "general.accounts_plural")}`;
    balance.accent = "var(--accent-color, #4ecca3)";
    balance.valclass = totalBalance >= 0 ? "pos" : "neg";

    const expenses = this.shadowRoot.getElementById("expenses");
    expenses.label = window._fd.tSync("stats.expenses");
    expenses.value = eur(totalExp);
    expenses.subtitle = window._fd.tSync("stats.transactions", { n: txnCount });
    expenses.accent = "var(--dg, #e74c3c)";
    expenses.valclass = "neg";

    const income = this.shadowRoot.getElementById("income");
    income.label = window._fd.tSync("stats.income");
    income.value = eur(totalInc);
    income.subtitle = window._fd.tSync("stats.net");
    income.accent = "var(--bl, #3b82f6)";
    income.valclass = "";

    const savings = this.shadowRoot.getElementById("savings");
    savings.label = window._fd.tSync("stats.savings_rate");
    savings.value = `${savingsRate}%`;
    savings.subtitle = window._fd.tSync("stats.month_balance", { amount: `${surplus >= 0 ? "+" : ""}${eur(surplus)}` });
    savings.accent = "var(--pp, #8b5cf6)";
    savings.valclass = "";
  }
  _renderSkeleton() {
    const { SHARED_CSS } = window._fd;
    const LOCAL_CSS = `
:host { margin-bottom: 20px; }
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
@media (max-width: 768px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
}
`;
    this.shadowRoot.innerHTML = `
<style>${SHARED_CSS}${LOCAL_CSS}</style>
<div class="stats">
  <fd-stat-card id="balance"></fd-stat-card>
  <fd-stat-card id="expenses"></fd-stat-card>
  <fd-stat-card id="income"></fd-stat-card>
  <fd-stat-card id="savings"></fd-stat-card>
</div>`;
    ["balance", "expenses", "income", "savings"].forEach((id) => {
      const el = this.shadowRoot.getElementById(id);
      if (el) {
        el.label = window._fd.tSync({ balance: "stats.balance", expenses: "stats.expenses", income: "stats.income", savings: "stats.savings_rate" }[id]);
        el.setData(null); // triggers skeleton
      }
    });
  }
}

customElements.define("fd-stats-row", FdStatsRow);

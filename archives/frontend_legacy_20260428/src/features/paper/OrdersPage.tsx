import type { PaperOrder } from "../../api/types";
import { StatusBadge } from "../../components/StatusBadge";
import { OrderNotionalChart } from "./PaperCharts";
import { formatCurrency, formatNumber } from "./format";

export function OrdersTable({ orders }: { orders: Array<PaperOrder & { strategy?: string }> }) {
  if (!orders.length) {
    return <div className="empty-state">No simulated orders on the latest run.</div>;
  }

  return (
    <section className="panel">
      <div className="panel__header">
        <h2>Latest Orders</h2>
        <span>{orders.length} orders</span>
      </div>
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Instrument</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Notional</th>
              <th>Commission</th>
              <th>Execution</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order, index) => (
              <tr key={`${order.strategy}-${order.instrument}-${index}`}>
                <td>{order.strategy ?? "-"}</td>
                <td>{order.instrument ?? "-"}</td>
                <td>
                  <StatusBadge label={order.side ?? "-"} tone={String(order.side).toLowerCase() === "buy" ? "positive" : "negative"} />
                </td>
                <td>{formatNumber(Number(order.quantity ?? 0))}</td>
                <td>{formatCurrency(Number(order.notional ?? 0))}</td>
                <td>{formatCurrency(Number(order.commission ?? 0))}</td>
                <td>{formatCurrency(Number(order.execution_price ?? 0))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function OrdersPage({ orders }: { orders: Array<PaperOrder & { strategy?: string }> }) {
  return (
    <div className="orders-page">
      <section className="panel">
        <div className="panel__header">
          <h2>Order Notional By Strategy</h2>
          <span>Latest simulated execution</span>
        </div>
        <OrderNotionalChart orders={orders} />
      </section>
      <OrdersTable orders={orders} />
    </div>
  );
}

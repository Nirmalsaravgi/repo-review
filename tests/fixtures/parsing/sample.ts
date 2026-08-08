/** Payment surface for tests. */
export interface PaymentGateway {
  charge(amount: number): Promise<void>;
}

export function charge(amount: number): number {
  return amount * 100;
}

export class StripeClient {
  async pay(cents: number): Promise<void> {
    return;
  }
}

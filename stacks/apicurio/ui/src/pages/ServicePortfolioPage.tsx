import { useParams } from 'react-router-dom';
import { ServicePortfolio } from '@/features/services';

export function ServicePortfolioPage() {
  const { urn } = useParams<{ urn: string }>();
  return <ServicePortfolio urn={urn!} />;
}

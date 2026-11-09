// Based on the Pydantic model in the backend

export interface Task {
  id: number;
  task_name: string;
  enabled: boolean;
  keyword: string;
  description: string;
  analyze_images: boolean;
  max_pages: number;
  personal_only: boolean;
  min_price: string | null;
  max_price: string | null;
  cron: string | null;
  next_run_at?: string | null;
  ai_prompt_base_file: string;
  ai_prompt_criteria_file: string;
  account_state_file?: string | null;
  account_strategy: 'auto' | 'fixed' | 'rotate';
  free_shipping?: boolean;
  new_publish_option?: string | null;
  region?: string | null;
  product_selection?: { catalog_id: string; memory_gb: number; storage_gb: number } | null;
  monitor_mode?: 'market' | 'buying';
  decision_mode: 'ai' | 'keyword';
  keyword_rules: string[];
  is_running: boolean;
}

export interface TaskCreateResponse {
  message: string;
  task?: Task;
}

// For PATCH requests, all fields are optional
export type TaskUpdate = Partial<Omit<Task, 'id' | 'next_run_at'>>;

// For task creation
export interface TaskGenerateRequest {
  product_selection?: { catalog_id: string; memory_gb: number; storage_gb: number } | null;
  monitor_mode?: 'market' | 'buying';
  task_name: string;
  keyword: string;
  description?: string;
  analyze_images?: boolean;
  personal_only?: boolean;
  min_price?: string | null;
  max_price?: string | null;
  max_pages?: number;
  cron?: string | null;
  account_state_file?: string | null;
  account_strategy?: 'auto' | 'fixed' | 'rotate';
  free_shipping?: boolean;
  new_publish_option?: string | null;
  region?: string | null;
  decision_mode?: 'ai' | 'keyword';
  keyword_rules?: string[];
}

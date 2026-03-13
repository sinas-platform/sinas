import axios, { type AxiosInstance } from 'axios';
import type {
  LoginRequest,
  LoginResponse,
  OTPVerifyRequest,
  OTPVerifyResponse,
  User,
  UserCreate,
  APIKey,
  APIKeyCreate,
  APIKeyCreatedResponse,
  Chat,
  ChatCreate,
  ChatUpdate,
  ChatWithMessages,
  Message,
  MessageSendRequest,
  Agent,
  AgentCreate,
  AgentUpdate,
  Role,
  RoleCreate,
  UserRole,
  RolePermission,
  RolePermissionUpdate,
  Function,
  FunctionCreate,
  FunctionUpdate,
  Webhook,
  WebhookCreate,
  WebhookUpdate,
  LLMProvider,
  LLMProviderCreate,
  LLMProviderUpdate,
  Template,
  TemplateCreate,
  TemplateUpdate,
  TemplateRenderResponse,
  Skill,
  SkillCreate,
  SkillUpdate,
  Component,
  ComponentCreate,
  ComponentUpdate,
  Collection,
  CollectionCreate,
  CollectionUpdate,
  FileInfo,
  FileWithVersions,
  FileUploadRequest,
  FileDownloadResponse,
  FileSearchRequest,
  FileSearchResult,
  Manifest,
  ManifestCreate,
  ManifestUpdate,
  ManifestStatus,
  DatabaseConnection,
  DatabaseConnectionCreate,
  DatabaseConnectionUpdate,
  DatabaseConnectionTestResponse,
  SchemaInfo,
  DbTableInfo,
  DbTableDetail,
  DbViewInfo,
  BrowseRowsResponse,
  FilterCondition,
  CreateTableRequest,
  AlterTableRequest,
  CreateViewRequest,
  AnnotationItem,
  Query,
  QueryCreate,
  QueryUpdate,
  QueryExecuteResponse,
} from '../types';

// Auto-detect API base URL based on environment
// Local: http://localhost:8000
// Production: https://yourdomain.com (same domain as console, port 443)
export const API_BASE_URL = window.location.hostname === 'localhost'
  ? 'http://localhost:8000'
  : `${window.location.protocol}//${window.location.hostname}`;

/**
 * Build a component render URL using a signed render token.
 * Follows the same pattern as file serve tokens - purpose-scoped, short-lived JWTs
 * that allow iframe access without Authorization headers.
 */
export function getComponentRenderUrl(renderToken: string, namespace: string, name: string, input?: Record<string, unknown>): string {
  const params = new URLSearchParams();
  params.set('token', renderToken);
  if (input) params.set('input', JSON.stringify(input));
  return `${API_BASE_URL}/components/${namespace}/${name}/render?${params.toString()}`;
}

const CONFIG_API_BASE_URL = `${API_BASE_URL}/api/v1`;
const RUNTIME_API_BASE_URL = API_BASE_URL;

class APIClient {
  private configClient: AxiosInstance;  // For management/config APIs
  private runtimeClient: AxiosInstance; // For runtime agent operations
  private errorHandler: ((message: string) => void) | null = null;
  private isRefreshing = false;
  private failedQueue: Array<{
    resolve: (value?: any) => void;
    reject: (reason?: any) => void;
  }> = [];

  constructor() {
    // Config/Management API client
    this.configClient = axios.create({
      baseURL: CONFIG_API_BASE_URL,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Runtime API client
    this.runtimeClient = axios.create({
      baseURL: RUNTIME_API_BASE_URL,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Add interceptors to both clients
    [this.configClient, this.runtimeClient].forEach(client => {
      this.setupInterceptors(client);
    });
  }

  private setupInterceptors(client: AxiosInstance) {
    // Add auth token interceptor
    client.interceptors.request.use((config) => {
      const token = localStorage.getItem('auth_token');
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
      return config;
    });

    // Add error interceptor for 401 and detailed error messages
    client.interceptors.response.use(
      (response) => response,
      async (error) => {
        const originalRequest = error.config;

        // Handle 401 errors with token refresh
        if (error.response?.status === 401 && !originalRequest._retry) {
          if (this.isRefreshing) {
            // Wait for refresh to complete, then retry
            return new Promise((resolve, reject) => {
              this.failedQueue.push({ resolve, reject });
            })
              .then(() => client(originalRequest))
              .catch((err) => Promise.reject(err));
          }

          originalRequest._retry = true;
          this.isRefreshing = true;

          const refreshToken = localStorage.getItem('refresh_token');

          if (!refreshToken) {
            this.clearAuthAndRedirect();
            return Promise.reject(error);
          }

          try {
            // Attempt to refresh the token (use runtime API)
            const response = await axios.post(`${API_BASE_URL}/auth/refresh`, {
              refresh_token: refreshToken,
            });

            const { access_token } = response.data;
            localStorage.setItem('auth_token', access_token);

            // Retry all queued requests
            this.processQueue(null);

            // Retry the original request
            originalRequest.headers.Authorization = `Bearer ${access_token}`;
            return client(originalRequest);
          } catch (refreshError) {
            // Refresh failed, clear auth and redirect
            this.processQueue(refreshError);
            this.clearAuthAndRedirect();
            return Promise.reject(refreshError);
          } finally {
            this.isRefreshing = false;
          }
        }

        // Enhance error with detailed message from response
        let errorMessage = error.message;
        if (error.response?.data) {
          const detail = error.response.data.detail || error.response.data.message;
          if (detail) {
            errorMessage = typeof detail === 'string'
              ? detail
              : JSON.stringify(detail, null, 2);
          } else {
            // Include the entire response body if no detail field
            errorMessage = `${error.message}\n\nResponse: ${JSON.stringify(error.response.data, null, 2)}`;
          }
          error.message = errorMessage;
        }

        // Show error toast notification (except for 401 which is handled above)
        if (error.response?.status !== 401 && this.errorHandler) {
          this.errorHandler(errorMessage);
        }

        return Promise.reject(error);
      }
    );
  }

  private processQueue(error: any) {
    this.failedQueue.forEach((prom) => {
      if (error) {
        prom.reject(error);
      } else {
        prom.resolve();
      }
    });
    this.failedQueue = [];
  }

  private clearAuthAndRedirect() {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    window.location.href = '/login';
  }

  setErrorHandler(handler: (message: string) => void) {
    this.errorHandler = handler;
  }

  // Authentication (Runtime API)
  async login(data: LoginRequest): Promise<LoginResponse> {
    const response = await this.runtimeClient.post('/auth/login', data);
    return response.data;
  }

  async verifyOTP(data: OTPVerifyRequest): Promise<OTPVerifyResponse> {
    const response = await this.runtimeClient.post('/auth/verify-otp', data);
    return response.data;
  }

  async getCurrentUser(): Promise<User> {
    const response = await this.runtimeClient.get('/auth/me');
    return response.data;
  }

  async refreshToken(refreshToken: string): Promise<{ access_token: string; expires_in: number }> {
    const response = await this.runtimeClient.post('/auth/refresh', { refresh_token: refreshToken });
    return response.data;
  }

  async logout(refreshToken: string): Promise<void> {
    await this.runtimeClient.post('/auth/logout', { refresh_token: refreshToken });
  }

  // API Keys
  async listAPIKeys(): Promise<APIKey[]> {
    const response = await this.configClient.get('/api-keys');
    return response.data;
  }

  async createAPIKey(data: APIKeyCreate): Promise<APIKeyCreatedResponse> {
    const response = await this.configClient.post('/api-keys', data);
    return response.data;
  }

  async revokeAPIKey(keyId: string): Promise<void> {
    await this.configClient.delete(`/api-keys/${keyId}`);
  }

  // Chats (Runtime API)
  async listChats(includeArchived: boolean = false): Promise<Chat[]> {
    const params = includeArchived ? { include_archived: true } : {};
    const response = await this.runtimeClient.get('/chats', { params });
    return response.data;
  }

  async getChat(chatId: string): Promise<ChatWithMessages> {
    const response = await this.runtimeClient.get(`/chats/${chatId}`);
    return response.data;
  }

  async createChatWithAgent(namespace: string, name: string, data: ChatCreate): Promise<Chat> {
    const response = await this.runtimeClient.post(`/agents/${namespace}/${name}/chats`, data);
    return response.data;
  }

  async updateChat(chatId: string, data: ChatUpdate): Promise<Chat> {
    const response = await this.runtimeClient.put(`/chats/${chatId}`, data);
    return response.data;
  }

  async deleteChat(chatId: string): Promise<void> {
    await this.runtimeClient.delete(`/chats/${chatId}`);
  }

  async sendMessage(chatId: string, data: MessageSendRequest): Promise<Message> {
    const response = await this.runtimeClient.post(`/chats/${chatId}/messages`, data);
    return response.data;
  }

  async listMessages(chatId: string): Promise<Message[]> {
    const response = await this.runtimeClient.get(`/chats/${chatId}/messages`);
    return response.data;
  }

  // Agents
  async listAgents(): Promise<Agent[]> {
    const response = await this.configClient.get('/agents');
    return response.data;
  }

  async getAgent(namespace: string, name: string): Promise<Agent> {
    const response = await this.configClient.get(`/agents/${namespace}/${name}`);
    return response.data;
  }

  async createAgent(data: AgentCreate): Promise<Agent> {
    const response = await this.configClient.post('/agents', data);
    return response.data;
  }

  async updateAgent(namespace: string, name: string, data: AgentUpdate): Promise<Agent> {
    const response = await this.configClient.put(`/agents/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteAgent(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/agents/${namespace}/${name}`);
  }

  // Roles
  async listRoles(): Promise<Role[]> {
    const response = await this.configClient.get('/roles');
    return response.data;
  }

  async getRole(roleId: string): Promise<any> {
    const response = await this.configClient.get(`/roles/${roleId}`);
    return response.data;
  }

  async createRole(data: RoleCreate): Promise<Role> {
    const response = await this.configClient.post('/roles', data);
    return response.data;
  }

  async updateRole(roleName: string, data: any): Promise<Role> {
    const response = await this.configClient.patch(`/roles/${roleName}`, data);
    return response.data;
  }

  async deleteRole(roleName: string): Promise<void> {
    await this.configClient.delete(`/roles/${roleName}`);
  }

  // Role Members
  async listRoleMembers(roleName: string): Promise<UserRole[]> {
    const response = await this.configClient.get(`/roles/${roleName}/members`);
    return response.data;
  }

  async addRoleMember(roleName: string, data: any): Promise<any> {
    const response = await this.configClient.post(`/roles/${roleName}/members`, data);
    return response.data;
  }

  async removeRoleMember(roleName: string, userId: string): Promise<void> {
    await this.configClient.delete(`/roles/${roleName}/members/${userId}`);
  }

  // Role Permissions
  async listRolePermissions(roleName: string): Promise<RolePermission[]> {
    const response = await this.configClient.get(`/roles/${roleName}/permissions`);
    return response.data;
  }

  async setRolePermission(roleName: string, data: RolePermissionUpdate): Promise<RolePermission> {
    const response = await this.configClient.post(`/roles/${roleName}/permissions`, data);
    return response.data;
  }

  async deleteRolePermission(roleName: string, permissionKey: string): Promise<void> {
    await this.configClient.delete(`/roles/${roleName}/permissions`, {
      params: { permission_key: permissionKey }
    });
  }

  async getPermissionReference(): Promise<Array<{ resource: string; description: string; actions: string[]; namespaced?: boolean; adminOnly?: boolean }>> {
    const response = await this.configClient.get('/roles/permissions/reference');
    return response.data;
  }

  async getPermissionDefaults(): Promise<Record<string, Record<string, boolean>>> {
    const response = await this.configClient.get('/roles/permissions/defaults');
    return response.data;
  }

  // Users
  async listUsers(params?: { skip?: number; limit?: number; search?: string }): Promise<any[]> {
    const response = await this.configClient.get('/users', { params });
    return response.data;
  }

  async createUser(data: UserCreate): Promise<User> {
    const response = await this.configClient.post('/users', data);
    return response.data;
  }

  async getUser(userId: string): Promise<any> {
    const response = await this.configClient.get(`/users/${userId}`);
    return response.data;
  }

  async updateUser(userId: string, data: any): Promise<any> {
    const response = await this.configClient.patch(`/users/${userId}`, data);
    return response.data;
  }

  async deleteUser(userId: string): Promise<void> {
    await this.configClient.delete(`/users/${userId}`);
  }

  // Request Logs
  async listRequestLogs(params?: {
    user_id?: string;
    start_time?: string;
    end_time?: string;
    permission?: string;
    path_pattern?: string;
    status_code?: number;
    limit?: number;
    offset?: number;
  }): Promise<any[]> {
    const response = await this.configClient.get('/request-logs', { params });
    return response.data;
  }

  async getRequestLogStats(params?: {
    user_id?: string;
    start_time?: string;
    end_time?: string;
  }): Promise<any> {
    const response = await this.configClient.get('/request-logs/stats', { params });
    return response.data;
  }

  // State Store (Runtime API - formerly Context Store)
  async listStates(params?: {
    namespace?: string;
    visibility?: string;
    skip?: number;
    limit?: number;
  }): Promise<any[]> {
    const response = await this.runtimeClient.get('/states', { params });
    return response.data;
  }

  async getState(stateId: string): Promise<any> {
    const response = await this.runtimeClient.get(`/states/${stateId}`);
    return response.data;
  }

  async createState(data: any): Promise<any> {
    const response = await this.runtimeClient.post('/states', data);
    return response.data;
  }

  async updateState(stateId: string, data: any): Promise<any> {
    const response = await this.runtimeClient.put(`/states/${stateId}`, data);
    return response.data;
  }

  async deleteState(stateId: string): Promise<void> {
    await this.runtimeClient.delete(`/states/${stateId}`);
  }

  // Store management (admin CRUD)
  async listStores(params?: { namespace?: string }): Promise<any[]> {
    const response = await this.configClient.get('/stores', { params });
    return response.data;
  }

  async getStore(namespace: string, name: string): Promise<any> {
    const response = await this.configClient.get(`/stores/${namespace}/${name}`);
    return response.data;
  }

  async createStore(data: any): Promise<any> {
    const response = await this.configClient.post('/stores', data);
    return response.data;
  }

  async updateStore(namespace: string, name: string, data: any): Promise<any> {
    const response = await this.configClient.put(`/stores/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteStore(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/stores/${namespace}/${name}`);
  }

  // Store-based state operations (runtime)
  async listStoreStates(namespace: string, name: string, params?: any): Promise<any[]> {
    const response = await this.runtimeClient.get(`/stores/${namespace}/${name}/states`, { params });
    return response.data;
  }

  async createStoreState(namespace: string, name: string, data: any): Promise<any> {
    const response = await this.runtimeClient.post(`/stores/${namespace}/${name}/states`, data);
    return response.data;
  }

  async updateStoreState(namespace: string, name: string, key: string, data: any): Promise<any> {
    const response = await this.runtimeClient.put(`/stores/${namespace}/${name}/states/${key}`, data);
    return response.data;
  }

  async deleteStoreState(namespace: string, name: string, key: string): Promise<void> {
    await this.runtimeClient.delete(`/stores/${namespace}/${name}/states/${key}`);
  }

  // Functions
  async listFunctions(): Promise<Function[]> {
    const response = await this.configClient.get('/functions');
    return response.data;
  }

  async getFunction(namespace: string, name: string): Promise<Function> {
    const response = await this.configClient.get(`/functions/${namespace}/${name}`);
    return response.data;
  }

  async createFunction(data: FunctionCreate): Promise<Function> {
    const response = await this.configClient.post('/functions', data);
    return response.data;
  }

  async updateFunction(namespace: string, name: string, data: FunctionUpdate): Promise<Function> {
    const response = await this.configClient.put(`/functions/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteFunction(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/functions/${namespace}/${name}`);
  }

  async executeFunction(namespace: string, name: string, inputData: any): Promise<any> {
    const response = await this.runtimeClient.post(`/functions/${namespace}/${name}/execute`, { input: inputData });
    return response.data;
  }

  async importOpenAPI(data: import('../types').OpenAPIImportRequest): Promise<import('../types').OpenAPIImportResponse> {
    const response = await this.configClient.post('/functions/import/openapi', data);
    return response.data;
  }

  // Webhooks
  async listWebhooks(): Promise<Webhook[]> {
    const response = await this.configClient.get('/webhooks');
    return response.data;
  }

  async getWebhook(path: string): Promise<Webhook> {
    const response = await this.configClient.get(`/webhooks/${path}`);
    return response.data;
  }

  async createWebhook(data: WebhookCreate): Promise<Webhook> {
    const response = await this.configClient.post('/webhooks', data);
    return response.data;
  }

  async updateWebhook(path: string, data: WebhookUpdate): Promise<Webhook> {
    const response = await this.configClient.patch(`/webhooks/${path}`, data);
    return response.data;
  }

  async deleteWebhook(path: string): Promise<void> {
    await this.configClient.delete(`/webhooks/${path}`);
  }

  // Schedules
  async listSchedules(): Promise<any[]> {
    const response = await this.configClient.get('/schedules');
    return response.data;
  }

  async getSchedule(scheduleId: string): Promise<any> {
    const response = await this.configClient.get(`/schedules/${encodeURIComponent(scheduleId)}`);
    return response.data;
  }

  async createSchedule(data: any): Promise<any> {
    const response = await this.configClient.post('/schedules', data);
    return response.data;
  }

  async updateSchedule(scheduleId: string, data: any): Promise<any> {
    const response = await this.configClient.patch(`/schedules/${encodeURIComponent(scheduleId)}`, data);
    return response.data;
  }

  async deleteSchedule(scheduleId: string): Promise<void> {
    await this.configClient.delete(`/schedules/${encodeURIComponent(scheduleId)}`);
  }

  // Database Triggers (CDC)
  async listDatabaseTriggers(): Promise<any[]> {
    const response = await this.configClient.get('/database-triggers');
    return response.data;
  }

  async getDatabaseTrigger(name: string): Promise<any> {
    const response = await this.configClient.get(`/database-triggers/${encodeURIComponent(name)}`);
    return response.data;
  }

  async createDatabaseTrigger(data: any): Promise<any> {
    const response = await this.configClient.post('/database-triggers', data);
    return response.data;
  }

  async updateDatabaseTrigger(name: string, data: any): Promise<any> {
    const response = await this.configClient.patch(`/database-triggers/${encodeURIComponent(name)}`, data);
    return response.data;
  }

  async deleteDatabaseTrigger(name: string): Promise<void> {
    await this.configClient.delete(`/database-triggers/${encodeURIComponent(name)}`);
  }

  // Executions
  async listExecutions(): Promise<any[]> {
    const response = await this.runtimeClient.get('/executions');
    return response.data;
  }

  async getExecution(executionId: string): Promise<any> {
    const response = await this.runtimeClient.get(`/executions/${executionId}`);
    return response.data;
  }

  // Dependencies (pip packages for function containers)
  async listDependencies(): Promise<any[]> {
    const response = await this.configClient.get('/dependencies');
    return response.data;
  }

  async installDependency(data: any): Promise<any> {
    const response = await this.configClient.post('/dependencies', data);
    return response.data;
  }

  async deleteDependency(dependencyId: string): Promise<void> {
    await this.configClient.delete(`/dependencies/${dependencyId}`);
  }

  async reloadWorkers(): Promise<any> {
    const response = await this.configClient.post('/workers/reload');
    return response.data;
  }

  // Packages (installable integration packages)
  async listPackages(): Promise<any[]> {
    const response = await this.configClient.get('/packages');
    return response.data;
  }

  async getPackage(name: string): Promise<any> {
    const response = await this.configClient.get(`/packages/${name}`);
    return response.data;
  }

  async installPackage(source: string): Promise<any> {
    const response = await this.configClient.post('/packages/install', { source });
    return response.data;
  }

  async previewPackage(source: string): Promise<any> {
    const response = await this.configClient.post('/packages/preview', { source });
    return response.data;
  }

  async createPackageYaml(data: any): Promise<any> {
    const response = await this.configClient.post('/packages/create', data);
    return response.data;
  }

  async uninstallPackage(name: string): Promise<any> {
    const response = await this.configClient.delete(`/packages/${name}`);
    return response.data;
  }

  async exportPackage(name: string): Promise<string> {
    const response = await this.configClient.get(`/packages/${name}/export`);
    return response.data;
  }

  // LLM Providers
  async listLLMProviders(): Promise<LLMProvider[]> {
    const response = await this.configClient.get('/llm-providers');
    return response.data;
  }

  async getLLMProvider(providerId: string): Promise<LLMProvider> {
    const response = await this.configClient.get(`/llm-providers/${providerId}`);
    return response.data;
  }

  async createLLMProvider(data: LLMProviderCreate): Promise<LLMProvider> {
    const response = await this.configClient.post('/llm-providers', data);
    return response.data;
  }

  async updateLLMProvider(providerId: string, data: LLMProviderUpdate): Promise<LLMProvider> {
    const response = await this.configClient.patch(`/llm-providers/${providerId}`, data);
    return response.data;
  }

  async deleteLLMProvider(providerId: string): Promise<void> {
    await this.configClient.delete(`/llm-providers/${providerId}`);
  }

  // Database Connections
  async listDatabaseConnections(): Promise<DatabaseConnection[]> {
    const response = await this.configClient.get('/database-connections');
    return response.data;
  }

  async getDatabaseConnection(name: string): Promise<DatabaseConnection> {
    const response = await this.configClient.get(`/database-connections/${name}`);
    return response.data;
  }

  async createDatabaseConnection(data: DatabaseConnectionCreate): Promise<DatabaseConnection> {
    const response = await this.configClient.post('/database-connections', data);
    return response.data;
  }

  async updateDatabaseConnection(id: string, data: DatabaseConnectionUpdate): Promise<DatabaseConnection> {
    const response = await this.configClient.patch(`/database-connections/${id}`, data);
    return response.data;
  }

  async deleteDatabaseConnection(id: string): Promise<void> {
    await this.configClient.delete(`/database-connections/${id}`);
  }

  async testDatabaseConnection(id: string): Promise<DatabaseConnectionTestResponse> {
    const response = await this.configClient.post(`/database-connections/${id}/test`);
    return response.data;
  }

  async testDatabaseConnectionRaw(data: {
    connection_type: string;
    host: string;
    port: number;
    database: string;
    username: string;
    password?: string;
    ssl_mode?: string;
  }): Promise<DatabaseConnectionTestResponse> {
    const response = await this.configClient.post('/database-connections/test', data);
    return response.data;
  }

  // Database Schema Browser
  async listDbSchemas(connectionName: string): Promise<SchemaInfo[]> {
    const response = await this.configClient.get(`/database-connections/${connectionName}/schemas`);
    return response.data;
  }

  async listDbTables(connectionName: string, schema: string = 'public'): Promise<DbTableInfo[]> {
    const response = await this.configClient.get(`/database-connections/${connectionName}/tables`, { params: { schema } });
    return response.data;
  }

  async getDbTableDetail(connectionName: string, table: string, schema: string = 'public'): Promise<DbTableDetail> {
    const response = await this.configClient.get(`/database-connections/${connectionName}/tables/${table}`, { params: { schema } });
    return response.data;
  }

  async listDbViews(connectionName: string, schema: string = 'public'): Promise<DbViewInfo[]> {
    const response = await this.configClient.get(`/database-connections/${connectionName}/views`, { params: { schema } });
    return response.data;
  }

  async createDbTable(connectionName: string, data: CreateTableRequest): Promise<any> {
    const response = await this.configClient.post(`/database-connections/${connectionName}/tables`, data);
    return response.data;
  }

  async alterDbTable(connectionName: string, table: string, data: AlterTableRequest): Promise<any> {
    const response = await this.configClient.patch(`/database-connections/${connectionName}/tables/${table}`, data);
    return response.data;
  }

  async dropDbTable(connectionName: string, table: string, schema: string = 'public', cascade: boolean = false): Promise<void> {
    await this.configClient.delete(`/database-connections/${connectionName}/tables/${table}`, { params: { schema, cascade } });
  }

  async createDbView(connectionName: string, data: CreateViewRequest): Promise<any> {
    const response = await this.configClient.post(`/database-connections/${connectionName}/views`, data);
    return response.data;
  }

  async dropDbView(connectionName: string, view: string, schema: string = 'public', cascade: boolean = false): Promise<void> {
    await this.configClient.delete(`/database-connections/${connectionName}/views/${view}`, { params: { schema, cascade } });
  }

  async browseDbRows(
    connectionName: string,
    table: string,
    params: { schema?: string; limit?: number; offset?: number; sort_by?: string; sort_order?: string; filters?: FilterCondition[] } = {}
  ): Promise<BrowseRowsResponse> {
    const { filters, ...rest } = params;
    const queryParams: Record<string, any> = { ...rest };
    if (filters && filters.length > 0) {
      queryParams.filters = JSON.stringify(filters);
    }
    const response = await this.configClient.get(`/database-connections/${connectionName}/tables/${table}/rows`, { params: queryParams });
    return response.data;
  }

  async insertDbRows(connectionName: string, table: string, rows: Record<string, any>[], schema: string = 'public'): Promise<any> {
    const response = await this.configClient.post(`/database-connections/${connectionName}/tables/${table}/rows`, { rows }, { params: { schema } });
    return response.data;
  }

  async updateDbRows(connectionName: string, table: string, where: Record<string, any>, set_values: Record<string, any>, schema: string = 'public'): Promise<any> {
    const response = await this.configClient.patch(`/database-connections/${connectionName}/tables/${table}/rows`, { where, set_values }, { params: { schema } });
    return response.data;
  }

  async deleteDbRows(connectionName: string, table: string, where: Record<string, any>, schema: string = 'public'): Promise<any> {
    const response = await this.configClient.delete(`/database-connections/${connectionName}/tables/${table}/rows`, { data: { where }, params: { schema } });
    return response.data;
  }

  async getDbAnnotations(connectionName: string): Promise<AnnotationItem[]> {
    const response = await this.configClient.get(`/database-connections/${connectionName}/annotations`);
    return response.data;
  }

  async upsertDbAnnotations(connectionName: string, annotations: AnnotationItem[]): Promise<any> {
    const response = await this.configClient.put(`/database-connections/${connectionName}/annotations`, { annotations });
    return response.data;
  }

  // Queries
  async listQueries(): Promise<Query[]> {
    const response = await this.configClient.get('/queries');
    return response.data;
  }

  async getQuery(namespace: string, name: string): Promise<Query> {
    const response = await this.configClient.get(`/queries/${namespace}/${name}`);
    return response.data;
  }

  async createQuery(data: QueryCreate): Promise<Query> {
    const response = await this.configClient.post('/queries', data);
    return response.data;
  }

  async updateQuery(namespace: string, name: string, data: QueryUpdate): Promise<Query> {
    const response = await this.configClient.put(`/queries/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteQuery(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/queries/${namespace}/${name}`);
  }

  async executeQuery(namespace: string, name: string, input: Record<string, any> = {}): Promise<QueryExecuteResponse> {
    const response = await this.configClient.post(`/queries/${namespace}/${name}/execute`, { input });
    return response.data;
  }

  // Config Management
  async validateConfig(config: string): Promise<any> {
    const response = await this.configClient.post('/config/validate', { config });
    return response.data;
  }

  async applyConfig(config: string, dryRun: boolean = false, force: boolean = false): Promise<any> {
    const response = await this.configClient.post('/config/apply', { config, dryRun, force });
    return response.data;
  }

  async exportConfig(includeSecrets: boolean = false, managedOnly: boolean = false): Promise<string> {
    const response = await this.configClient.get('/config/export', {
      params: { include_secrets: includeSecrets, managed_only: managedOnly }
    });
    return response.data;
  }

  // Workers
  async listWorkers(): Promise<any[]> {
    const response = await this.configClient.get('/workers');
    return response.data;
  }

  async getWorkerCount(): Promise<{ count: number }> {
    const response = await this.configClient.get('/workers/count');
    return response.data;
  }

  async scaleWorkers(targetCount: number): Promise<any> {
    const response = await this.configClient.post('/workers/scale', { target_count: targetCount });
    return response.data;
  }

  // Queue
  async getQueueStats(): Promise<any> {
    const response = await this.configClient.get('/queue/stats');
    return response.data;
  }

  async getQueueJobs(status?: string): Promise<any[]> {
    const params = status ? { status } : {};
    const response = await this.configClient.get('/queue/jobs', { params });
    return response.data;
  }

  async getQueueDLQ(): Promise<any[]> {
    const response = await this.configClient.get('/queue/dlq');
    return response.data;
  }

  async cancelJob(jobId: string): Promise<any> {
    const response = await this.configClient.post(`/queue/jobs/${jobId}/cancel`);
    return response.data;
  }

  async retryDLQJob(jobId: string): Promise<any> {
    const response = await this.configClient.post(`/queue/dlq/${jobId}/retry`);
    return response.data;
  }

  async getQueueWorkers(): Promise<any[]> {
    const response = await this.configClient.get('/queue/workers');
    return response.data;
  }

  // Container Pool
  // System Health
  async getSystemHealth(): Promise<any> {
    const response = await this.configClient.get('/system/health');
    return response.data;
  }

  async restartContainer(containerName: string): Promise<any> {
    const response = await this.configClient.post(`/system/containers/${containerName}/restart`);
    return response.data;
  }

  async flushDLQ(): Promise<any> {
    const response = await this.configClient.post('/queue/dlq/flush');
    return response.data;
  }

  async flushStuckJobs(): Promise<any> {
    const response = await this.configClient.post('/system/flush-stuck-jobs');
    return response.data;
  }

  async getContainerStats(): Promise<any> {
    const response = await this.configClient.get('/containers/stats');
    return response.data;
  }

  async scaleContainerPool(target: number): Promise<any> {
    const response = await this.configClient.post('/containers/scale', { target });
    return response.data;
  }

  // Skills
  async listSkills(): Promise<Skill[]> {
    const response = await this.configClient.get('/skills');
    return response.data;
  }

  async getSkill(namespace: string, name: string): Promise<Skill> {
    const response = await this.configClient.get(`/skills/${namespace}/${name}`);
    return response.data;
  }

  async createSkill(data: SkillCreate): Promise<Skill> {
    const response = await this.configClient.post('/skills', data);
    return response.data;
  }

  async updateSkill(namespace: string, name: string, data: SkillUpdate): Promise<Skill> {
    const response = await this.configClient.put(`/skills/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteSkill(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/skills/${namespace}/${name}`);
  }

  // Components
  async listComponents(): Promise<Component[]> {
    const response = await this.configClient.get('/components');
    return response.data;
  }

  async getComponent(namespace: string, name: string): Promise<Component> {
    const response = await this.configClient.get(`/components/${namespace}/${name}`);
    return response.data;
  }

  async createComponent(data: ComponentCreate): Promise<Component> {
    const response = await this.configClient.post('/components', data);
    return response.data;
  }

  async updateComponent(namespace: string, name: string, data: ComponentUpdate): Promise<Component> {
    const response = await this.configClient.put(`/components/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteComponent(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/components/${namespace}/${name}`);
  }

  async compileComponent(namespace: string, name: string): Promise<Component> {
    const response = await this.configClient.post(`/components/${namespace}/${name}/compile`);
    return response.data;
  }

  // Templates
  async listTemplates(): Promise<Template[]> {
    const response = await this.configClient.get('/templates');
    return response.data;
  }

  async getTemplate(templateId: string): Promise<Template> {
    const response = await this.configClient.get(`/templates/${templateId}`);
    return response.data;
  }

  async getTemplateByName(templateName: string): Promise<Template> {
    const response = await this.configClient.get(`/templates/by-name/${templateName}`);
    return response.data;
  }

  async createTemplate(data: TemplateCreate): Promise<Template> {
    const response = await this.configClient.post('/templates', data);
    return response.data;
  }

  async updateTemplate(templateId: string, data: TemplateUpdate): Promise<Template> {
    const response = await this.configClient.patch(`/templates/${templateId}`, data);
    return response.data;
  }

  async deleteTemplate(templateId: string): Promise<void> {
    await this.configClient.delete(`/templates/${templateId}`);
  }

  async renderTemplate(templateId: string, variables: Record<string, any>): Promise<TemplateRenderResponse> {
    const response = await this.configClient.post(`/templates/${templateId}/render`, { variables });
    return response.data;
  }

  // Messages (analytics/search across all messages)
  async searchMessages(params?: {
    agent?: string;
    role?: string;
    search?: string;
    limit?: number;
    offset?: number;
  }): Promise<any> {
    const response = await this.configClient.get('/messages', { params });
    return response.data;
  }

  // Collections
  async listCollections(params?: { namespace?: string }): Promise<Collection[]> {
    const response = await this.configClient.get('/collections', { params });
    return response.data;
  }

  async getCollection(namespace: string, name: string): Promise<Collection> {
    const response = await this.configClient.get(`/collections/${namespace}/${name}`);
    return response.data;
  }

  async createCollection(data: CollectionCreate): Promise<Collection> {
    const response = await this.configClient.post('/collections', data);
    return response.data;
  }

  async updateCollection(namespace: string, name: string, data: CollectionUpdate): Promise<Collection> {
    const response = await this.configClient.put(`/collections/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteCollection(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/collections/${namespace}/${name}`);
  }

  // Files (Runtime API)
  async listFiles(namespace: string, collection: string): Promise<FileWithVersions[]> {
    const response = await this.runtimeClient.get(`/files/${namespace}/${collection}`);
    return response.data;
  }

  async uploadFile(namespace: string, collection: string, data: FileUploadRequest): Promise<FileInfo> {
    const response = await this.runtimeClient.post(`/files/${namespace}/${collection}`, data);
    return response.data;
  }

  async downloadFile(namespace: string, collection: string, filename: string, version?: number): Promise<FileDownloadResponse> {
    const params = version ? { version } : {};
    const response = await this.runtimeClient.get(`/files/${namespace}/${collection}/${filename}`, { params });
    return response.data;
  }

  async deleteFile(namespace: string, collection: string, filename: string): Promise<void> {
    await this.runtimeClient.delete(`/files/${namespace}/${collection}/${filename}`);
  }

  async updateFileMetadata(namespace: string, collection: string, filename: string, metadata: Record<string, any>): Promise<FileInfo> {
    const response = await this.runtimeClient.patch(`/files/${namespace}/${collection}/${filename}`, { file_metadata: metadata });
    return response.data;
  }

  async searchFiles(namespace: string, collection: string, request: FileSearchRequest): Promise<FileSearchResult[]> {
    const response = await this.runtimeClient.post(`/files/${namespace}/${collection}/search`, request);
    return response.data;
  }

  async generateFileUrl(namespace: string, collection: string, filename: string, version?: number, expiresIn?: number): Promise<{ url: string; filename: string; content_type: string; version: number; expires_in: number }> {
    const params: Record<string, any> = {};
    if (version) params.version = version;
    if (expiresIn) params.expires_in = expiresIn;
    const response = await this.runtimeClient.post(`/files/${namespace}/${collection}/${filename}/url`, null, { params });
    return response.data;
  }

  // Manifests
  async listManifests(namespace?: string): Promise<Manifest[]> {
    const params = namespace ? { namespace } : {};
    const response = await this.configClient.get('/manifests', { params });
    return response.data;
  }

  async getManifest(namespace: string, name: string): Promise<Manifest> {
    const response = await this.configClient.get(`/manifests/${namespace}/${name}`);
    return response.data;
  }

  async createManifest(data: ManifestCreate): Promise<Manifest> {
    const response = await this.configClient.post('/manifests', data);
    return response.data;
  }

  async updateManifest(namespace: string, name: string, data: ManifestUpdate): Promise<Manifest> {
    const response = await this.configClient.put(`/manifests/${namespace}/${name}`, data);
    return response.data;
  }

  async deleteManifest(namespace: string, name: string): Promise<void> {
    await this.configClient.delete(`/manifests/${namespace}/${name}`);
  }

  async getManifestStatus(namespace: string, name: string): Promise<ManifestStatus> {
    const response = await this.runtimeClient.get(`/manifests/${namespace}/${name}/status`);
    return response.data;
  }
}


export const apiClient = new APIClient();

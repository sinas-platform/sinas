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
  MCPServer,
  MCPServerCreate,
  MCPServerUpdate,
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
} from '../types';

// Auto-detect API base URL based on environment
// Local: http://localhost:8000
// Production: https://yourdomain.com (same domain as console, port 443)
const API_BASE_URL = window.location.hostname === 'localhost'
  ? 'http://localhost:8000'
  : `${window.location.protocol}//${window.location.hostname}`;

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
    const response = await this.configClient.get('/auth/api-keys');
    return response.data;
  }

  async createAPIKey(data: APIKeyCreate): Promise<APIKeyCreatedResponse> {
    const response = await this.configClient.post('/auth/api-keys', data);
    return response.data;
  }

  async revokeAPIKey(keyId: string): Promise<void> {
    await this.configClient.delete(`/auth/api-keys/${keyId}`);
  }

  // Chats (Runtime API)
  async listChats(): Promise<Chat[]> {
    const response = await this.runtimeClient.get('/chats');
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

  // MCP Servers
  async listMCPServers(): Promise<MCPServer[]> {
    const response = await this.configClient.get('/mcp/servers');
    return response.data;
  }

  async getMCPServer(serverId: string): Promise<MCPServer> {
    const response = await this.configClient.get(`/mcp/servers/${serverId}`);
    return response.data;
  }

  async createMCPServer(data: MCPServerCreate): Promise<MCPServer> {
    const response = await this.configClient.post('/mcp/servers', data);
    return response.data;
  }

  async updateMCPServer(serverId: string, data: MCPServerUpdate): Promise<MCPServer> {
    const response = await this.configClient.put(`/mcp/servers/${serverId}`, data);
    return response.data;
  }

  async deleteMCPServer(serverId: string): Promise<void> {
    await this.configClient.delete(`/mcp/servers/${serverId}`);
  }

  async listMCPTools(): Promise<any[]> {
    const response = await this.configClient.get('/mcp/tools');
    return response.data;
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
    await this.configClient.delete(`/roles/${roleName}/permissions/${encodeURIComponent(permissionKey)}`);
  }

  // Users
  async listUsers(): Promise<any[]> {
    const response = await this.configClient.get('/users');
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
    const response = await this.configClient.post(`/functions/${namespace}/${name}/execute`, inputData);
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
    const response = await this.configClient.get(`/schedules/${scheduleId}`);
    return response.data;
  }

  async createSchedule(data: any): Promise<any> {
    const response = await this.configClient.post('/schedules', data);
    return response.data;
  }

  async updateSchedule(scheduleId: string, data: any): Promise<any> {
    const response = await this.configClient.patch(`/schedules/${scheduleId}`, data);
    return response.data;
  }

  async deleteSchedule(scheduleId: string): Promise<void> {
    await this.configClient.delete(`/schedules/${scheduleId}`);
  }

  // Executions
  async listExecutions(): Promise<any[]> {
    const response = await this.configClient.get('/executions');
    return response.data;
  }

  async getExecution(executionId: string): Promise<any> {
    const response = await this.configClient.get(`/executions/${executionId}`);
    return response.data;
  }

  // Packages
  async listPackages(): Promise<any[]> {
    const response = await this.configClient.get('/packages');
    return response.data;
  }

  async installPackage(data: any): Promise<any> {
    const response = await this.configClient.post('/packages', data);
    return response.data;
  }

  async deletePackage(packageId: string): Promise<void> {
    await this.configClient.delete(`/packages/${packageId}`);
  }

  async reloadWorkers(): Promise<any> {
    const response = await this.configClient.post('/workers/reload');
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
}


export const apiClient = new APIClient();

#!/bin/bash

# Bulk refactor: Groups → Roles
# This script does search/replace for the groups→roles refactor

set -e

echo "Starting Groups → Roles refactor..."

# Function to remove group resolution blocks in endpoints
remove_group_resolution() {
    local file=$1
    echo "Processing: $file"

    # Remove group resolution logic (the pattern we saw in functions.py)
    perl -i -0777 -pe 's/\n\s*# Resolve group_name.*?\n\s*final_group_id = .*?\n\s*if .*?group_name.*?\n.*?from app\.models\.user import Group\n.*?result = await db\.execute\(.*?\n.*?select\(Group\.id\)\.where\(Group\.name == .*?group_name\).*?\n.*?\)\n.*?group = result\.scalar_one_or_none\(\)\n.*?if not group:\n.*?raise HTTPException.*?Group.*?not found.*?\n.*?final_group_id = group\n//g' "$file"

    # Remove group_id from model instantiation
    sed -i '' 's/group_id=final_group_id,//' "$file"
    sed -i '' 's/,\s*group_id=final_group_id//' "$file"

    # Remove group name loading blocks
    perl -i -0777 -pe 's/\n\s*# Load group name.*?\n\s*response = FunctionResponse.*?\n\s*if .*?\.group_id:\n.*?from app\.models\.user import Group\n.*?result = await db\.execute\(.*?\n.*?select\(Group\.name\).*?\n.*?\)\n.*?group_name = result\.scalar.*?\n.*?response\.group_name = .*?\n\n\s*return response/\n    return FunctionResponse.model_validate(function)/g' "$file"

    perl -i -0777 -pe 's/\n\s*if .*?\.group_id:\n.*?from app\.models\.user import Group\n.*?result = await db\.execute\(.*?\n.*?select\(Group\.name\).*?\n.*?\)\n.*?response\.group_name = result\.scalar.*?\n.*?responses\.append\(response\)/\n        responses.append(response)/g' "$file"

    # Update group resolution in update endpoints
    perl -i -0777 -pe 's/\n\s*# Resolve group_name to group_id.*?\n\s*if .*?group_name is not None:\n.*?from app\.models\.user import Group\n.*?result = await db\.execute\(.*?\n.*?select\(Group\.(id|name)\)\.where\(Group\.name == .*?group_name\).*?\n.*?\)\n.*?group = result\.scalar_one_or_none\(\)\n.*?if not group:\n.*?raise HTTPException.*?Group.*?not found.*?\n.*?\.group_id = group\n.*?elif .*?group_id is not None:\n.*?\.group_id = .*?group_id\n//g' "$file"
}

# Remove :group scope from permission checks
fix_permission_checks() {
    local file=$1
    echo "Fixing permissions in: $file"

    # Remove :group permission checks and associated logic
    sed -i '' '/elif check_permission(permissions.*:group")/,/query = select/d' "$file"
    sed -i '' 's/"own and group-accessible"/"own or all based on permissions"/' "$file"
    sed -i '' 's/# Can see own and group functions//' "$file"
    sed -i '' 's/# TODO: Get user.*groups//' "$file"
}

# Process agents endpoint
if [ -f "app/api/v1/endpoints/agents.py" ]; then
    echo "Processing agents.py..."
    remove_group_resolution "app/api/v1/endpoints/agents.py"
    fix_permission_checks "app/api/v1/endpoints/agents.py"
fi

# Process webhooks endpoint
if [ -f "app/api/v1/endpoints/webhooks.py" ]; then
    echo "Processing webhooks.py..."
    remove_group_resolution "app/api/v1/endpoints/webhooks.py"
fi

# Process schedules endpoint
if [ -f "app/api/v1/endpoints/schedules.py" ]; then
    echo "Processing schedules.py..."
    remove_group_resolution "app/api/v1/endpoints/schedules.py"
fi

# Process templates endpoint
if [ -f "app/api/v1/endpoints/templates.py" ]; then
    echo "Processing templates.py..."
    # Just remove group references, templates has different patterns
    sed -i '' '/elif check_permission.*:group/,/query = select/d' "app/api/v1/endpoints/templates.py"
fi

echo "✓ Endpoint group resolution removed"

# Rename groups.py to roles.py and update imports
if [ -f "app/api/v1/endpoints/groups.py" ]; then
    echo "Renaming groups.py → roles.py..."
    cp app/api/v1/endpoints/groups.py app/api/v1/endpoints/roles.py

    # Update the roles.py file
    sed -i '' 's/from app\.models\.user import Group, GroupMember, GroupPermission/from app.models.user import Role, UserRole, RolePermission/' app/api/v1/endpoints/roles.py
    sed -i '' 's/from app\.schemas\.group import/from app.schemas.role import/' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupCreate/RoleCreate/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupUpdate/RoleUpdate/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupResponse/RoleResponse/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupMember/UserRole/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupMemberResponse/UserRoleResponse/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupMemberAdd/UserRoleAdd/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupPermission/RolePermission/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupPermissionResponse/RolePermissionResponse/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/GroupPermissionUpdate/RolePermissionUpdate/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/\"groups\"/\"roles\"/' app/api/v1/endpoints/roles.py
    sed -i '' 's/\/groups/\/roles/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/router = APIRouter(prefix="\/groups"/router = APIRouter(prefix="\/roles"/' app/api/v1/endpoints/roles.py
    sed -i '' 's/tags=\["groups"\]/tags=["roles"]/' app/api/v1/endpoints/roles.py

    # Update Group → Role (model references)
    sed -i '' 's/select(Group)/select(Role)/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/Group\.get_by_name/Role.get_by_name/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/Group(/Role(/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/ Group / Role /g' app/api/v1/endpoints/roles.py

    # Variable name updates
    sed -i '' 's/group_data/role_data/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/\bgroup\b/role/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/group_id/role_id/g' app/api/v1/endpoints/roles.py
    sed -i '' 's/group_name/role_name/g' app/api/v1/endpoints/roles.py

    echo "✓ roles.py created"
fi

echo ""
echo "Refactor complete! Now run manual verification..."
echo ""
echo "Files modified:"
ls -lh app/api/v1/endpoints/{agents,webhooks,schedules,templates,roles}.py

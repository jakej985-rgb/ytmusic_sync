import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../services/api_service.dart';

class AccountSelectorWidget extends StatefulWidget {
  final ValueChanged<SelectableAccountItem?>? onAccountChanged;
  final VoidCallback? onFamilyModeSelected;

  const AccountSelectorWidget({
    super.key,
    this.onAccountChanged,
    this.onFamilyModeSelected,
  });

  @override
  State<AccountSelectorWidget> createState() => _AccountSelectorWidgetState();
}

class _AccountSelectorWidgetState extends State<AccountSelectorWidget> {
  List<SelectableAccountItem> _accounts = [];
  SelectableAccountItem? _selectedAccount;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _loadAccounts();
  }

  Future<void> _loadAccounts() async {
    if (!mounted) return;
    setState(() => _isLoading = true);
    try {
      final accounts = await apiService.getSelectableAccounts();
      if (!mounted) return;
      setState(() {
        _accounts = accounts;
        // Default to self
        _selectedAccount = accounts.firstWhere(
          (a) => a.isSelf,
          orElse: () => accounts.isNotEmpty ? accounts.first : _createSelfFallback(),
        );
        _isLoading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  SelectableAccountItem _createSelfFallback() {
    final user = apiService.currentUser;
    return SelectableAccountItem(
      userId: user?.id ?? '',
      username: user?.username ?? 'You',
      accountName: apiService.ytmAccount?.accountName,
      accountIdentifier: apiService.ytmAccount?.accountEmail,
      isConnected: apiService.ytmAccount?.isConnected ?? false,
      isSelf: true,
      allowFamilyUploads: true,
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading && _accounts.isEmpty) {
      return const SizedBox(
        height: 36,
        width: 140,
        child: Center(child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))),
      );
    }

    final currentDisplay = _selectedAccount != null
        ? (_selectedAccount!.isSelf ? 'My Account (${_selectedAccount!.username})' : _selectedAccount!.displayName)
        : 'Select Account';

    return Container(
      height: 36,
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E28),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white12),
      ),
      child: PopupMenuButton<dynamic>(
        tooltip: 'Switch Target Account / Family Mode',
        offset: const Offset(0, 42),
        color: const Color(0xFF222230),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                _selectedAccount?.isSelf == true ? Icons.person : Icons.group,
                size: 16,
                color: const Color(0xFF3EA6FF),
              ),
              const SizedBox(width: 8),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 160),
                child: Text(
                  currentDisplay,
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Colors.white),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 4),
              const Icon(Icons.arrow_drop_down, size: 18, color: Colors.grey),
            ],
          ),
        ),
        onSelected: (val) {
          if (val == '__family_mode__') {
            widget.onFamilyModeSelected?.call();
          } else if (val is SelectableAccountItem) {
            setState(() => _selectedAccount = val);
            widget.onAccountChanged?.call(val);
          }
        },
        itemBuilder: (ctx) {
          final items = <PopupMenuEntry<dynamic>>[];

          items.add(
            const PopupMenuItem<dynamic>(
              enabled: false,
              height: 28,
              child: Text(
                'TARGET ACCOUNT',
                style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Colors.grey, letterSpacing: 1),
              ),
            ),
          );

          for (final acc in _accounts) {
            final isCurrent = acc.userId == _selectedAccount?.userId;
            items.add(
              PopupMenuItem<dynamic>(
                value: acc,
                child: Row(
                  children: [
                    Icon(
                      acc.isSelf ? Icons.person : Icons.person_outline,
                      size: 16,
                      color: isCurrent ? const Color(0xFFFF0000) : Colors.white70,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            acc.displayName,
                            style: TextStyle(
                              fontSize: 13,
                              fontWeight: isCurrent ? FontWeight.bold : FontWeight.normal,
                              color: isCurrent ? Colors.white : Colors.white70,
                            ),
                          ),
                          if (acc.familyName != null)
                            Text(
                              acc.familyName!,
                              style: const TextStyle(fontSize: 10, color: Colors.grey),
                            ),
                        ],
                      ),
                    ),
                    if (isCurrent)
                      const Icon(Icons.check, size: 16, color: Color(0xFFFF0000))
                    else if (!acc.isConnected)
                      const Text('(disconnected)', style: TextStyle(fontSize: 10, color: Colors.amber)),
                  ],
                ),
              ),
            );
          }

          items.add(const PopupMenuDivider());

          items.add(
            const PopupMenuItem<dynamic>(
              value: '__family_mode__',
              child: Row(
                children: [
                  Icon(Icons.dashboard_customize, size: 16, color: Color(0xFF3EA6FF)),
                  SizedBox(width: 8),
                  Text(
                    'Family Mode Dashboard',
                    style: TextStyle(fontSize: 13, color: Color(0xFF3EA6FF), fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
          );

          return items;
        },
      ),
    );
  }
}

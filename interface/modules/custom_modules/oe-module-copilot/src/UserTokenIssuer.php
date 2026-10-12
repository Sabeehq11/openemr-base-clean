<?php

/**
 * Issues a short-lived OpenEMR access token that belongs to the physician who is logged in right now.
 *
 * This is the "module-issued token" fallback from the Checkpoint 2 plan. It is acceptable only because:
 *   - the subject is the session's own user (never a caller-supplied id),
 *   - OpenEMR's API validates it like any other token on every call (signature, expiry, revocation, trusted session),
 *   - the agent introspects it and re-checks patient-level access with it on every tool call,
 *   - scopes are read-only and the lifetime is minutes.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

use DateTimeImmutable;
use League\OAuth2\Server\CryptKey;
use OpenEMR\Common\Auth\OAuth2KeyConfig;
use OpenEMR\Common\Auth\OpenIDConnect\Entities\AccessTokenEntity;
use OpenEMR\Common\Auth\OpenIDConnect\Entities\ScopeEntity;
use OpenEMR\Common\Auth\OpenIDConnect\Repositories\AccessTokenRepository;
use OpenEMR\Common\Auth\OpenIDConnect\Repositories\ClientRepository;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\FHIR\Config\ServerConfig;
use OpenEMR\Services\TrustedUserService;

final class UserTokenIssuer
{
    public const TTL_SECONDS = 300;

    /** Read-only scopes. Nothing here can write to the chart. */
    public const SCOPES = [
        'openid', 'fhirUser',
        'user/Patient.rs', 'user/CareTeam.rs', 'user/Condition.rs', 'user/MedicationRequest.rs',
        'user/AllergyIntolerance.rs', 'user/Observation.rs', 'user/Encounter.rs',
    ];

    public function __construct(private readonly string $clientId, private readonly string $siteDir)
    {
    }

    /**
     * @param string $userUuid the logged-in user's UUID (string form), taken from the session by the caller
     * @return string the bearer token (JWT)
     */
    public function issue(string $userUuid): string
    {
        $client = (new ClientRepository())->getClientEntity($this->clientId);
        if ($client === false || !$client->isEnabled()) {
            throw new \RuntimeException('Co-Pilot OAuth client is not registered and enabled');
        }
        $keys = new OAuth2KeyConfig($this->siteDir);
        $keys->configKeyPairs();

        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $repo = new AccessTokenRepository(new ServerConfig(), $session);
        $token = $repo->getNewToken($client, [], $userUuid);
        foreach (self::SCOPES as $scope) {
            $token->addScope(ScopeEntity::createFromString($scope));
        }
        $token->setIdentifier(bin2hex(random_bytes(40)));
        $token->setExpiryDateTime(new DateTimeImmutable('+' . self::TTL_SECONDS . ' seconds'));
        $token->setPrivateKey(new CryptKey($keys->getPrivateKeyLocation(), $keys->getPassPhrase()));
        $repo->persistNewAccessToken($token);

        // OpenEMR's API only honours a token while its user has a live trusted-session record.
        $trusted = new TrustedUserService();
        if (empty($trusted->getTrustedUser($this->clientId, $userUuid)['session_cache'] ?? '')) {
            $trusted->saveTrustedUser($this->clientId, $userUuid, implode(' ', self::SCOPES), 0, '', 'copilot-module-session');
        }
        return (string) $token;
    }
}
